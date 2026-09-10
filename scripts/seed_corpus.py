#!/usr/bin/env python3
"""Generate the synthetic MRPL corpus and ingest it.

Idempotent: documents are content-addressed, so re-running replaces rather than
duplicates. Ownership and classification come from the generator's manifest, so
the corpus arrives with the same access controls a real one would have — which
is what makes the RBAC demonstration meaningful rather than decorative.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from sqlalchemy import select  # noqa: E402

from workbench.core.ids import prefixed_id  # noqa: E402
from workbench.core.logging import configure_logging  # noqa: E402
from workbench.db.models import Document, User  # noqa: E402
from workbench.db.session import init_engine, session_scope  # noqa: E402
from workbench.ingest.indexer import DocumentIndexer  # noqa: E402
from workbench.ingest.ocr.engine import build_engine  # noqa: E402
from workbench.ingest.pipeline import IngestionPipeline, PipelineConfig  # noqa: E402
from workbench.ingest.storage import BlobStore  # noqa: E402
from workbench.ingest.vision.reader import VisionReader  # noqa: E402
from workbench.providers.registry import ModelRegistry  # noqa: E402
from workbench.providers.residency import ResidencyManager  # noqa: E402
from workbench.rag.chunker import Chunker, ChunkSpec  # noqa: E402
from workbench.rag.embedder import Embedder  # noqa: E402
from workbench.rag.vectorstore.qdrant_store import QdrantStore  # noqa: E402
from workbench.settings import get_settings  # noqa: E402

CORPUS_DIR = REPO_ROOT / "data" / "seed" / "corpus"
TRUTH_DIR = REPO_ROOT / "data" / "seed" / "ground_truth"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true", help="rebuild the corpus files first")
    parser.add_argument("--no-vision", action="store_true", help="skip VLM escalation (faster)")
    parser.add_argument("--only", help="ingest a single document id")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    settings.ensure_directories()

    if args.regenerate or not (TRUTH_DIR / "doc_manifest.json").is_file():
        print("generating corpus files...")
        # Awaited rather than run with subprocess.run: this function is async,
        # and a blocking call here stalls the event loop that the ingest below
        # runs on. The argv is fixed — this interpreter and a path inside the
        # repo — so there is nothing untrusted to inject.
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(REPO_ROOT / "data/seed/generator/render.py"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            print(stderr.decode(errors="replace"), file=sys.stderr)
            raise SystemExit(f"corpus generation failed ({process.returncode})")

    manifest = json.loads((TRUTH_DIR / "doc_manifest.json").read_text(encoding="utf-8"))
    if args.only:
        manifest = [m for m in manifest if m["doc_id"] == args.only]
        if not manifest:
            print(f"no document '{args.only}' in the manifest", file=sys.stderr)
            return 1

    init_engine(settings)
    registry = ModelRegistry(settings.models_manifest)
    await registry.probe_availability()
    residency = ResidencyManager(registry, max_resident_gb=14.0)

    embed_model = registry.get_model("embed.primary")
    embedder = Embedder(registry.provider_for("embed.primary"), embed_model)
    store = QdrantStore(settings.qdrant_url, embedder.collection)
    await store.ensure_collection(embedder.dimensions)

    vision = None if args.no_vision else VisionReader(registry=registry, residency=residency)
    pipeline = IngestionPipeline(
        blob_store=BlobStore(settings.blob_dir),
        page_image_dir=settings.page_image_dir,
        dataset_dir=settings.data_dir / "datasets",
        chunker=Chunker(ChunkSpec(max_child_tokens=int(embed_model.context_window * 0.9))),
        ocr_engine=build_engine("rapidocr"),
        vision_reader=vision,
        config=PipelineConfig(vision_enabled=not args.no_vision),
    )
    indexer = DocumentIndexer(embedder=embedder, vector_store=store)

    async with session_scope() as session:
        owner = (
            await session.execute(select(User).where(User.username == "senior"))
        ).scalar_one_or_none()
        owner_id = owner.id if owner else None

    print(
        f"\n{'document':<20} {'type':<15} {'class':<13} {'pages':>5} {'chunks':>7} {'conf':>6}  time"
    )
    print("-" * 84)

    total_started = time.perf_counter()
    ingested = skipped = 0

    for entry in manifest:
        path = CORPUS_DIR / entry["path"]
        if not path.is_file():
            print(f"{entry['doc_id']:<20} missing: {path}")
            continue

        data = path.read_bytes()
        from workbench.core.hashing import digest_bytes

        sha = digest_bytes(data)

        async with session_scope() as session:
            existing = (
                await session.execute(select(Document).where(Document.sha256 == sha))
            ).scalar_one_or_none()
            if existing is not None:
                # Content-addressed, so an unchanged file needs no work. Remove
                # the old rows first when it *has* changed.
                print(f"{entry['doc_id']:<20} already ingested, skipping")
                skipped += 1
                continue

        started = time.perf_counter()
        result = await pipeline.run(data=data, filename=path.name, doc_id=prefixed_id("document"))
        if not result.ok or result.ir is None:
            print(f"{entry['doc_id']:<20} FAILED")
            continue

        async with session_scope() as session:
            await indexer.index(
                session,
                ir=result.ir,
                chunks=result.chunks,
                sha256=result.sha256,
                filename=path.name,
                owner_user_id=owner_id,
                classification=entry["classification"],
                departments=entry["departments"],
                tags=entry["tags"],
                doc_type=entry["doc_type"],
                title=entry["title"],
                blob_path=str(BlobStore(settings.blob_dir).path_for(result.sha256)),
                size_bytes=len(data),
            )

        elapsed = time.perf_counter() - started
        report = result.ir.extraction_report
        marker = " *ocr" if report.pages_ocr else ""
        marker += " *vlm" if report.pages_vlm_escalated else ""
        print(
            f"{entry['doc_id']:<20} {entry['doc_type']:<15} {entry['classification']:<13} "
            f"{result.ir.page_count:>5} {len(result.chunks):>7} "
            f"{result.ir.mean_confidence:>6.2f}  {elapsed:>5.1f}s{marker}"
        )
        ingested += 1

    async with session_scope() as session:
        total_docs = len((await session.execute(select(Document))).scalars().all())

    print("-" * 84)
    print(
        f"ingested {ingested}, skipped {skipped}, corpus now {total_docs} documents "
        f"in {time.perf_counter() - total_started:.1f}s"
    )
    print(f"vectors in {embedder.collection}: {await store.count()}")

    await store.aclose()
    await registry.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
