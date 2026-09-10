"""Source content for the synthetic MRPL corpus.

Synthetic because real refinery documents are exactly what this system exists to
keep confidential — shipping any in a public repository would contradict the
premise. The structure is modelled on the real thing: API 510/570 inspection
language, OISD and IS references, and MRPL's equipment tag conventions
(V-1201 vessels, P-101A pumps, E-2405 exchangers), so retrieval and the tag
regex are exercised against realistic text.

Everything here is the ground truth for OCR evaluation. Documents are rendered
from these strings and then degraded, so character error rate is measured
against what the generator started from — exact, and free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from workbench.core.classification import Classification


@dataclass(frozen=True, slots=True)
class Section:
    heading: str
    level: int
    body: str = ""
    table: list[list[str]] | None = None


@dataclass(frozen=True, slots=True)
class SeedDocument:
    doc_id: str
    title: str
    doc_type: str
    classification: str
    departments: list[str]
    sections: list[Section]
    tags: list[str] = field(default_factory=list)
    #: Scan profile applied when rendering. "" means keep it a native PDF.
    scan_profile: str = ""

    @property
    def plain_text(self) -> str:
        """The exact text the document contains — the OCR ground truth."""
        parts: list[str] = [self.title]
        for section in self.sections:
            parts.append(section.heading)
            if section.body:
                parts.append(section.body)
            if section.table:
                parts.extend(" | ".join(row) for row in section.table)
        return "\n".join(parts)


THICKNESS_TABLE = [
    ["CML", "Location", "2019 (mm)", "2023 (mm)", "2029 (mm)", "t-min (mm)"],
    ["CML-01", "Top head", "14.20", "13.85", "13.40", "9.50"],
    ["CML-02", "Shell course 1", "14.10", "13.20", "12.15", "9.50"],
    ["CML-03", "Shell course 2", "14.05", "13.00", "11.80", "9.50"],
    ["CML-04", "Bottom shell", "13.90", "12.50", "9.20", "8.00"],
    ["CML-05", "Bottom head", "14.00", "13.60", "13.05", "8.00"],
    ["CML-06", "Nozzle N1 neck", "11.50", "11.10", "10.60", "7.00"],
]

MAINTENANCE_TABLE = [
    ["Date", "Tag", "Activity", "Findings", "Technician"],
    ["2029-01-14", "P-101A", "Vibration survey", "7.1 mm/s RMS — above 4.5 alert", "R. Krishnan"],
    ["2029-02-02", "P-101B", "Vibration survey", "2.8 mm/s RMS — normal", "R. Krishnan"],
    ["2029-02-18", "E-2405", "Tube bundle inspection", "12 tubes plugged, 4% of total", "S. Nair"],
    ["2029-03-06", "V-1201", "UT thickness survey", "CML-04 at 9.20 mm, approaching t-min", "S. Nair"],
    ["2029-03-22", "PSV-1207", "Bench test", "Set 18.5 barg, popped 18.9 barg — reset", "M. Devadiga"],
]


SEED_DOCUMENTS: list[SeedDocument] = [
    SeedDocument(
        doc_id="SOP-4412",
        title="SOP-4412 Vessel Shutdown and Depressurisation Procedure",
        doc_type="sop",
        classification=Classification.INTERNAL,
        departments=["operations", "inspection"],
        tags=["V-1201", "shutdown", "depressurisation"],
        sections=[
            Section("1. Purpose", 1,
                "This procedure defines the controlled shutdown and depressurisation of "
                "pressure vessel V-1201 in the Crude Distillation Unit. It applies to "
                "planned turnaround shutdowns and to controlled emergency depressurisation."),
            Section("2. Scope", 1,
                "Applies to V-1201 and its associated relief system PSV-1207. Does not "
                "cover emergency blowdown initiated by the Safety Instrumented System."),
            Section("3. Responsibilities", 1,
                "The Shift Superintendent authorises the shutdown. The Panel Operator "
                "executes the depressurisation sequence. The Inspection Engineer confirms "
                "that isolation is complete before any entry permit is issued."),
            Section("5. Shutdown Sequence", 1),
            Section("5.1 Feed Isolation", 2,
                "Close feed valve HV-1205 and confirm zero flow on FI-1205. Isolate the "
                "vessel from the flare header by closing XV-1207. Both valves shall be "
                "car-sealed closed and tagged before proceeding."),
            Section("5.2 Depressurisation", 2,
                "Depressurise through the 2-inch vent to flare. The depressurisation rate "
                "shall not exceed 2 bar per minute to avoid thermal shock to the shell. "
                "Hold at 4 barg for four hours at 120 degC to allow residual hydrocarbon "
                "to vaporise before final venting to atmosphere."),
            Section("5.3 Nitrogen Purge", 2,
                "Purge with nitrogen until the hydrocarbon content measured at the vent is "
                "below 1% LEL. A minimum of three volume changes is required. Record the "
                "final gas test on Form OISD-105."),
            Section("6. Safety Requirements", 1,
                "Confined space entry requires a valid permit, continuous atmospheric "
                "monitoring, and a standby attendant. Refer to OISD-STD-105 for hot work "
                "and IS 3844 for fire safety provisions."),
            Section("7. Retirement Criteria", 1,
                "The minimum required shell thickness for V-1201 is 8.0 mm as calculated "
                "per ASME Section VIII Division 1 UG-27. Any measurement at or below this "
                "value requires immediate fitness-for-service assessment before the vessel "
                "is returned to service."),
        ],
    ),
    SeedDocument(
        doc_id="INSP-2029-V1201",
        title="Inspection Report V-1201 — Shell Thickness Survey March 2029",
        doc_type="inspection",
        classification=Classification.CONFIDENTIAL,
        departments=["inspection"],
        tags=["V-1201", "UT", "corrosion"],
        scan_profile="photocopy",
        sections=[
            Section("1. Equipment Details", 1,
                "Equipment: V-1201 Crude Feed Surge Drum. Design pressure 18.0 barg. "
                "Design temperature 180 degC. Material SA-516 Gr.70. Nominal shell "
                "thickness 14.0 mm. Inside radius 1200 mm. Joint efficiency 0.85. "
                "Commissioned 2011."),
            Section("2. Inspection Method", 1,
                "Ultrasonic thickness measurement at six established condition monitoring "
                "locations, performed in accordance with API 510. Instrument: Olympus 38DL "
                "Plus, calibrated 2029-02-28. Surface prepared by wire brushing."),
            Section("3. Thickness Survey", 1, table=THICKNESS_TABLE),
            Section("4. Findings", 1,
                "CML-04 on the bottom shell shows the highest metal loss, measuring 9.20 mm "
                "against 12.50 mm recorded in 2023. This represents 3.30 mm of loss over "
                "six years. Localised pitting was observed adjacent to the bottom drain "
                "nozzle, consistent with water accumulation and under-deposit corrosion."),
            Section("5. Assessment", 1,
                "The corrosion rate at CML-04 is the controlling value for the vessel. "
                "Against a minimum required thickness of 8.00 mm, the remaining thickness "
                "available for corrosion is 1.20 mm. Remaining life should be recalculated "
                "and the next inspection interval set at no more than half that value."),
            Section("6. Recommendations", 1,
                "Increase inspection frequency at CML-04 to annual. Install a corrosion "
                "coupon in the bottom draw-off line. Review the water draw-off procedure "
                "with Operations. Consider internal coating at the next turnaround."),
            Section("7. Sign-off", 1,
                "Inspected by: S. Nair, Senior Inspection Engineer, Level II UT. "
                "Reviewed by: M. Devadiga, Maintenance Head. Date: 2029-03-06."),
        ],
    ),
    SeedDocument(
        doc_id="INSP-2023-V1201",
        title="Inspection Report V-1201 — Shell Thickness Survey March 2023",
        doc_type="inspection",
        classification=Classification.CONFIDENTIAL,
        departments=["inspection"],
        tags=["V-1201", "UT"],
        scan_profile="office",
        sections=[
            Section("1. Equipment Details", 1,
                "Equipment: V-1201 Crude Feed Surge Drum. Design pressure 18.0 barg. "
                "Material SA-516 Gr.70. Nominal shell thickness 14.0 mm."),
            Section("2. Thickness Survey", 1,
                "Measurements at the six established condition monitoring locations. "
                "CML-04 on the bottom shell measured 12.50 mm, down from 13.90 mm in 2019. "
                "All other locations remain above 13.00 mm."),
            Section("3. Findings", 1,
                "General corrosion is uniform and within expectation. The bottom shell "
                "shows a higher rate than the upper courses. No repairs required at this "
                "inspection. Next survey due 2029."),
            Section("4. Sign-off", 1,
                "Inspected by: S. Nair, Senior Inspection Engineer. Date: 2023-03-11."),
        ],
    ),
    SeedDocument(
        doc_id="MEMO-2029-014",
        title="Internal Memo — CDU Turnaround Scope and Budget 2029",
        doc_type="correspondence",
        classification=Classification.RESTRICTED,
        departments=["finance", "maintenance"],
        tags=["turnaround", "budget"],
        sections=[
            Section("Memorandum", 1,
                "From: General Manager (Maintenance). To: Head of Operations. "
                "Date: 2029-04-02. Subject: CDU turnaround scope and budget approval."),
            Section("1. Approved Budget", 1,
                "The approved turnaround budget for the Crude Distillation Unit is "
                "INR 14.2 crore. This figure is commercially sensitive and shall not be "
                "shared outside the Maintenance and Finance departments."),
            Section("2. Scope", 1,
                "Scope includes internal inspection and possible coating of V-1201, tube "
                "bundle replacement on E-2405, and overhaul of P-101A. Contractor "
                "mobilisation is scheduled for 2029-09-15."),
            Section("3. Catalyst", 1,
                "The proprietary catalyst formulation for the reformer is a 3:1 nickel to "
                "molybdenum ratio supplied under the confidentiality agreement with the "
                "licensor. Disclosure outside the process engineering group is prohibited."),
        ],
    ),
    SeedDocument(
        doc_id="MAINT-LOG-2029Q1",
        title="Maintenance Activity Log — Q1 2029",
        doc_type="tabular",
        classification=Classification.INTERNAL,
        departments=["maintenance", "inspection"],
        tags=["P-101A", "E-2405", "V-1201", "PSV-1207"],
        sections=[
            Section("Maintenance Log", 1, table=MAINTENANCE_TABLE),
            Section("Notes", 1,
                "P-101A vibration exceeded the 4.5 mm/s alert threshold and remains under "
                "watch. A bearing change is planned during the turnaround."),
        ],
    ),
    SeedDocument(
        doc_id="SOP-2210",
        title="SOP-2210 Centrifugal Pump Vibration Monitoring",
        doc_type="sop",
        classification=Classification.INTERNAL,
        departments=["maintenance"],
        tags=["P-101A", "vibration"],
        sections=[
            Section("1. Purpose", 1,
                "Defines routine vibration monitoring of centrifugal pumps in the Crude "
                "Distillation Unit, including P-101A and P-101B."),
            Section("2. Acceptance Criteria", 1,
                "Overall vibration velocity shall be assessed against ISO 10816-3 Zone "
                "boundaries. The alert threshold is 4.5 mm/s RMS. The shutdown threshold "
                "is 7.1 mm/s RMS. Readings are taken at the drive end and non-drive end in "
                "horizontal, vertical and axial directions."),
            Section("3. Frequency", 1,
                "Monthly for pumps in continuous service. Weekly once a reading exceeds "
                "the alert threshold, until the cause is corrected."),
        ],
    ),
]
