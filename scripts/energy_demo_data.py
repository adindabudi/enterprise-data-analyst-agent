"""Deterministic fictional upstream observations and a separately materialized SQL oracle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import cast

Row = dict[str, object]
Tables = dict[str, list[Row]]
Column = tuple[str, str, str, bool]

VERSION = "indonesia-upstream-synthetic-1.0.0"
AS_OF = "2026-09-01T00:00:00Z"
DISCLAIMER = (
    "Entirely synthetic, self-authored training observations for FICTIONAL Indonesian analogue fields; "
    "not operator data, reserves, acquired seismic, a safety assessment, or investment advice. "
    "Public publications provide geological context only, not statistical distributions or field measurements."
)
STANDARD_CONDITIONS = "Synthetic reporting basis: 60 degF, 14.696 psia; dry gas; gas volume unit MMscf."
SOURCES: list[Row] = [
    {
        "id": "IPA-PETANI-1994",
        "url": "https://www.ipa.or.id/en/publications/"
        "petani-horizontal-well-management-strategy-to-recover-hydrocarbon-in-menggala-formation",
        "context": "Petani Menggala approximately 3920 ft TVD (1195 m); datum unspecified, not TVDSS.",
    },
    {
        "id": "IPA-CS-PRESSURE-1995",
        "url": "https://www.ipa.or.id/en/publications/"
        "central-sumatra-prospect-evaluation-structural-and-stratigraphic-fluid-barriers-and-hydrodynamic-"
        "systems-as-indicated-by-wireline-formation-pressures",
        "context": "Case-specific aquifer depletion up to 600 psi, not a universal depletion assumption.",
    },
    {
        "id": "IPA-MINAS-2001",
        "url": "https://www.ipa.or.id/en/publications/"
        "propellant-stimulation-technique-provides-alternate-productivity-enhancement-with-cost-reduction-"
        "benefits-in-development-of-a-marginal-reservoir-at-minas-field",
        "context": "Minas X sand: porosity 20%, permeability <15 mD, thickness 20 ft, resistivity <10 ohm.m. "
        "Reported >100 bopd is incremental, not baseline production.",
    },
    {
        "id": "IPA-GELAM-2009",
        "url": "https://www.ipa.or.id/en/publications/"
        "a-reservoir-engineering-study-of-gelam-field-to-understand-compartmentalization",
        "context": "Talang Akar retrograde gas-condensate; communication interpretation changed with evidence.",
    },
    {
        "id": "LEMIGAS-2022",
        "url": "https://journal.lemigas.esdm.go.id/index.php/SCOG/article/view/1258",
        "context": "Study-specific Talang Akar screening: phi >14%, k >10 mD, Vsh <30%, Sw <70%, "
        "salinity >10000 ppm. Not a universal cutoff.",
    },
    {
        "id": "IPA-LES-1995",
        "url": "https://www.ipa.or.id/en/publications/"
        "systematic-application-of-seismic-amplitude-analysis-for-exploration-in-the-upper-cibulakan-"
        "formation-example-from-gas-discovery-les-1-offshore-northwest-java",
        "context": "Upper Cibulakan marine sand at 800-2000 m subsea; 62.2 million cubic feet/day and "
        "1229 condensate bpd across FIVE tests, not daily field production.",
    },
    {
        "id": "IPA-U11-1994",
        "url": "https://www.ipa.or.id/en/publications/"
        "a-sequence-stratigraphic-model-of-the-upper-cibulakan-sandstones-main-interval-offshore-northwest-"
        "java-basin-insights-from-u-11-well",
        "context": "Sandstone packages 50-100 ft, shale up to 250 ft; gross packages are not net pay.",
    },
]


def _c(name: str, kind: str, description: str, nullable: bool = False) -> Column:
    return name, kind, description, nullable


# Descriptions specify table grain rather than placing answer summaries on entities.
SPECS: dict[str, tuple[str, str, bool, list[Column]]] = {
    "Field": (
        "FieldId",
        "One fictional field; coarse basin context, deliberately no actual asset coordinates.",
        True,
        [
            _c("FieldId", "String", "Fictional field identifier."),
            _c("Name", "String", "Fictional field name."),
            _c("Basin", "String", "Regional analogue basin, not precise location."),
            _c("HydrocarbonSystem", "String", "Oil or gas-condensate reporting system."),
            _c("Setting", "String", "Onshore/offshore analogue setting."),
        ],
    ),
    "Reservoir": (
        "ReservoirId",
        "One named fictional reservoir within one field; no production allocation or reserves.",
        True,
        [
            _c("ReservoirId", "String", "Reservoir identifier."),
            _c("FieldId", "String", "Parent field identifier."),
            _c("Name", "String", "Fictional stratigraphic reservoir name."),
            _c("Formation", "String", "Analogue formation name."),
            _c("ReferenceTopTvdssM", "Double", "Synthetic reference top, metres positive below mean sea level."),
            _c("PressureReferenceTvdssM", "Double", "Common pressure comparison depth within this reservoir."),
        ],
    ),
    "Well": (
        "WellId",
        "One well across all boreholes and time; no precomputed ranking, trend, or candidate flag.",
        True,
        [
            _c("WellId", "String", "Fictional well identifier; all 12 wells remain in monthly denominators."),
            _c("FieldId", "String", "Parent field identifier."),
            _c("Name", "String", "Well display name."),
            _c("ProductionSystem", "String", "Oil with associated gas or gas-condensate."),
            _c("FirstProductionDate", "DateTime", "Synthetic first production date."),
        ],
    ),
    "Wellbore": (
        "WellboreId",
        "One main borehole or sidetrack; all depths metres, measured from its stated drill-floor reference.",
        True,
        [
            _c("WellboreId", "String", "Borehole identifier."),
            _c("WellId", "String", "Parent well identifier."),
            _c("ParentWellboreId", "String", "Parent borehole for a sidetrack.", True),
            _c("BoreType", "String", "Main or Sidetrack."),
            _c("SurveyDate", "DateTime", "Synthetic survey effective date."),
            _c("TotalDepthMdM", "Double", "Along-hole total measured depth from reference."),
            _c("TotalDepthTvdM", "Double", "Vertical total depth below reference."),
            _c("ReferenceElevationM", "Double", "Drill-floor reference elevation positive above mean sea level."),
            _c("TotalDepthTvdssM", "Double", "TVD minus reference elevation; positive below mean sea level."),
            _c("DepthReference", "String", "Local synthetic drill-floor reference; not a geodetic survey."),
        ],
    ),
    "Completion": (
        "CompletionId",
        "One reservoir interval completion in one borehole with a half-open effective date range.",
        True,
        [
            _c("CompletionId", "String", "Completion interval identifier."),
            _c("WellboreId", "String", "Completed borehole."),
            _c("ReservoirId", "String", "Completed reservoir."),
            _c("EffectiveFrom", "DateTime", "Inclusive effective calendar-date label."),
            _c("EffectiveTo", "DateTime", "Exclusive effective calendar-date label; null means open-ended.", True),
            _c("TopMdM", "Double", "Perforation top measured depth below drill floor."),
            _c("BaseMdM", "Double", "Perforation base measured depth below drill floor."),
            _c("FlowConfiguration", "String", "Single or Commingled; not an allocation factor."),
            _c("AllocationEvidence", "String", "Whether independent zonal metering evidence exists."),
        ],
    ),
    "ProductionDay": (
        "ProductionDayId",
        "One canonical well/day observation, including explicit missing days; ONLY linked to Well, never Completion.",
        True,
        [
            _c("ProductionDayId", "String", "Canonical unique well and WIB production date key."),
            _c("WellId", "String", "Observed well; never a reservoir allocation."),
            _c("ProductionDate", "DateTime", "WIB calendar-date LABEL encoded at UTC midnight, not a period instant."),
            _c("ProductionCalendar", "String", "Asia/Jakarta (WIB, UTC+07), 00:00 to 24:00 local."),
            _c("PeriodStartUtc", "DateTime", "Actual reporting interval start instant in UTC."),
            _c("PeriodEndUtc", "DateTime", "Actual exclusive reporting interval end instant in UTC."),
            _c("OilStb", "Double", "Integrated stock-tank oil volume for this day; excludes condensate.", True),
            _c("WaterBbl", "Double", "Integrated produced-water volume for this day.", True),
            _c("GasMMscf", "Double", "Integrated dry gas volume, MILLION standard cubic feet.", True),
            _c("CondensateStb", "Double", "Integrated stock-tank condensate volume, separate from oil.", True),
            _c("OperatingHours", "Double", "Observed onstream hours in the 24-hour production day.", True),
            _c("Quality", "String", "Validated, RevisedValidated, ShutdownValidated, or Missing."),
            _c("MeterBasis", "String", "Actual synthetic observation measurement/allocation method."),
            _c("Revision", "BigInt", "Current canonical revision; earlier report values live only in documents."),
            _c("RecordedAt", "DateTime", "Timestamp when this canonical observation was finalized."),
        ],
    ),
    "WellTest": (
        "WellTestId",
        "One finite-duration well test, not a production-day volume; rates normalized to standard conditions.",
        True,
        [
            _c("WellTestId", "String", "Test identifier."),
            _c("WellboreId", "String", "Tested borehole."),
            _c("TestDate", "DateTime", "UTC timestamp of test start."),
            _c("DurationHours", "Double", "Actual test duration; normalized rates are not this period's volume."),
            _c("Choke64thsIn", "BigInt", "Choke opening in 64ths of an inch."),
            _c("Stabilized", "Boolean", "Whether test stabilization criterion was met."),
            _c("OilRateStbPerDay", "Double", "Oil test rate equivalent to 24 hours."),
            _c("WaterRateBblPerDay", "Double", "Water test rate equivalent to 24 hours."),
            _c("GasRateMMscfPerDay", "Double", "Dry gas test rate, MMscf/day, not Mscf/day."),
            _c("CondensateRateStbPerDay", "Double", "Condensate test rate equivalent to 24 hours."),
            _c(
                "FlowingPressurePsia",
                "Double",
                "Flowing gauge pressure expressed absolute; NOT static reservoir pressure.",
            ),
            _c("PressureLocation", "String", "Gauge location and datum for flowing pressure."),
            _c(
                "Conditions", "String", "Test separator, export constraint, stabilization and standard-condition basis."
            ),
            _c("DocumentId", "String", "Supporting test report."),
        ],
    ),
    "LogInterval": (
        "LogIntervalId",
        "One interpreted log interval in a borehole, not proved pay or reserves; MD and TVDSS recorded separately.",
        True,
        [
            _c("LogIntervalId", "String", "Log interpretation interval identifier."),
            _c("WellboreId", "String", "Logged borehole."),
            _c("ReservoirId", "String", "Interpreted reservoir correlation, not proof of hydraulic communication."),
            _c("LogDate", "DateTime", "Log interpretation effective date."),
            _c("TopMdM", "Double", "Along-hole top below borehole drill floor."),
            _c("BaseMdM", "Double", "Along-hole base below borehole drill floor."),
            _c("TopTvdssM", "Double", "Vertical top positive below mean sea level."),
            _c("BaseTvdssM", "Double", "Vertical base positive below mean sea level."),
            _c("PorosityFraction", "Double", "Effective porosity fraction (not percent)."),
            _c("PermeabilityMd", "Double", "Interpreted permeability in millidarcies."),
            _c("WaterSaturationFraction", "Double", "Interpreted water saturation fraction."),
            _c("ShaleVolumeFraction", "Double", "Interpreted shale volume fraction."),
            _c("FormationWaterSalinityPpm", "Double", "Assumed formation water salinity for log interpretation."),
            _c("ResistivityOhmM", "Double", "Synthetic interval deep resistivity."),
            _c("Method", "String", "Interpretation method and calibration limitations."),
            _c("DocumentId", "String", "Supporting interpretation note."),
        ],
    ),
    "FluidSample": (
        "FluidSampleId",
        "One versioned lab interpretation of a sampled fluid and accompanying shut-in pressure observation.",
        True,
        [
            _c("FluidSampleId", "String", "Version-specific fluid report identifier."),
            _c("SampleGroupId", "String", "Same physical sample across report revisions."),
            _c("WellId", "String", "Sampled well."),
            _c("ReservoirId", "String", "Sample interval reservoir; may not imply interwell communication."),
            _c("SampleDate", "DateTime", "Actual sample acquisition timestamp."),
            _c("SampleLocation", "String", "Sampling location; gas analysis basis."),
            _c("PressureMeasurementDate", "DateTime", "Acquisition time of the separate shut-in pressure observation."),
            _c("StaticPressurePsia", "Double", "Shut-in absolute pressure measured at PressureDatumTvdssM."),
            _c("PressureDatumTvdssM", "Double", "Measurement datum positive below sea level."),
            _c("PressureGradientPsiPerM", "Double", "Synthetic hydrostatic correction gradient, not universal."),
            _c("ReservoirTemperatureC", "Double", "Measured synthetic reservoir temperature."),
            _c("GasCo2MolPercent", "Double", "Dry gas carbon dioxide mole percent; not fraction."),
            _c("ReportDate", "DateTime", "Report issuance timestamp."),
            _c("ReportStatus", "String", "Approved, Superseded, or Draft; newest is not automatically approved."),
            _c("SupersedesFluidSampleId", "String", "Prior version replaced only by an approved report.", True),
            _c("Provenance", "String", "Self-authored lab method and measurement scope."),
            _c("DocumentId", "String", "Supporting lab or reservoir report."),
        ],
    ),
    "OperationalEvent": (
        "OperationalEventId",
        "One operational event or proposal with explicit date interval and approval; not a causal uplift estimate.",
        True,
        [
            _c("OperationalEventId", "String", "Event identifier."),
            _c("WellId", "String", "Affected well."),
            _c("StartDate", "DateTime", "Inclusive WIB calendar-date label encoded UTC midnight."),
            _c("EndDate", "DateTime", "Exclusive WIB calendar-date label encoded UTC midnight."),
            _c("EventType", "String", "Shutdown, CompressorCurtailment, Workover, or Proposal."),
            _c("Status", "String", "Completed or Planned."),
            _c("DowntimeReason", "String", "Recorded operational reason, not proof of all production variance."),
            _c("ApprovalStatus", "String", "ApprovedExecution, NotApproved, or PendingReview."),
            _c("Description", "String", "Observed work scope or proposal limitations."),
            _c("DocumentId", "String", "Supporting operational note."),
        ],
    ),
    "Document": (
        "DocumentId",
        "One self-authored source document VERSION; narrative sections are in documents/source-documents.json.",
        True,
        [
            _c("DocumentId", "String", "Version-specific source document identifier."),
            _c("FileName", "String", "PDF output filename for the independent renderer."),
            _c("Title", "String", "Source document title."),
            _c("Status", "String", "Approved, Superseded, Draft, or Reference."),
            _c("EffectiveDate", "DateTime", "Document effective date, distinct from observation date."),
            _c("SupersedesDocumentId", "String", "Prior approved/superseded document version.", True),
        ],
    ),
    "DocumentWell": (
        "DocumentWellId",
        "One unique document/well association; bridge only, not an ontology entity or production fact.",
        False,
        [
            _c("DocumentWellId", "String", "Single string bridge key."),
            _c("DocumentId", "String", "Associated document version."),
            _c("WellId", "String", "Associated well; documents can cover multiple wells."),
        ],
    ),
    "SeismicInterpretation": (
        "SeismicInterpretationId",
        "One versioned conceptual reservoir-top interpretation; illustrative quantiles, NOT acquired 3D seismic.",
        True,
        [
            _c("SeismicInterpretationId", "String", "Versioned conceptual interpretation identifier."),
            _c("ReservoirId", "String", "Interpreted reservoir."),
            _c("Version", "BigInt", "Interpretation version."),
            _c("EffectiveDate", "DateTime", "Interpretation date."),
            _c("Status", "String", "Current or Superseded."),
            _c("TopDepthQ10TvdssM", "Double", "10% nonexceedance quantile: P(depth <= q10)=0.10."),
            _c("TopDepthQ50TvdssM", "Double", "Median top depth below mean sea level."),
            _c("TopDepthQ90TvdssM", "Double", "90% nonexceedance quantile; q10 <= q50 <= q90."),
            _c("EvidenceKind", "String", "ConceptualSynthetic only; no seismic cube or validated 3D interpretation."),
            _c("UncertaintyExplanation", "String", "Depth uncertainty semantics and absence of spatial covariance."),
            _c("DocumentId", "String", "Conceptual interpretation caveats."),
        ],
    ),
}

DOCUMENT_SPECS: list[tuple[str, str, str, str, str | None, list[str]]] = [
    ("D-BASE", "Basis pelaporan dan inventaris fiktif", "Reference", "2026-06-01", None, ["ALL"]),
    ("D-CS-WO", "Seroja CS02 catatan workover selesai", "Approved", "2026-07-10", None, ["FIC-CS-02"]),
    ("D-CS-PROP", "Seroja CS03 usulan stimulasi belum disetujui", "Draft", "2026-08-27", None, ["FIC-CS-03"]),
    ("D-CS-COMM", "Seroja CS04 konfigurasi commingled", "Approved", "2026-08-01", None, ["FIC-CS-04"]),
    ("D-ALLOC-OLD", "CS04 laporan alokasi Juli versi 1", "Superseded", "2026-08-02", None, ["FIC-CS-04"]),
    (
        "D-ALLOC-CURRENT",
        "CS04 rekonsiliasi alokasi Juli versi 2",
        "Approved",
        "2026-08-06",
        "D-ALLOC-OLD",
        ["FIC-CS-04"],
    ),
    ("D-GAS-TEST", "Rimba uji separator dan kendala kompresor", "Approved", "2026-08-24", None, ["SS"]),
    ("D-CO2-OLD", "SS02 laporan CO2 versi 1", "Superseded", "2026-08-01", None, ["FIC-SS-02"]),
    ("D-CO2-CURRENT", "SS02 laporan CO2 koreksi disetujui", "Approved", "2026-08-04", "D-CO2-OLD", ["FIC-SS-02"]),
    ("D-CO2-DRAFT", "SS02 kromatografi ulang belum ditinjau", "Draft", "2026-08-29", None, ["FIC-SS-02"]),
    ("D-SS-CONNECT", "Rimba penilaian komunikasi reservoir sementara", "Approved", "2026-08-26", None, ["SS"]),
    ("D-LOG", "Interpretasi log dan interval belum diperforasi", "Approved", "2026-08-25", None, ["ALL"]),
    ("D-JV-OPS", "Laut Lazuardi catatan operasi Agustus", "Approved", "2026-08-25", None, ["JV"]),
    ("D-MISSING", "JV02 insiden data harian 17 Agustus", "Approved", "2026-08-19", None, ["FIC-JV-02"]),
    ("D-SEISMIC", "Interpretasi struktur konseptual dan batasan 3D", "Reference", "2026-08-20", None, ["ALL"]),
]


def _utc(day: str | date) -> str:
    return f"{day}T00:00:00Z"


def _entity(**values: object) -> Row:
    return {**values, "Synthetic": True}


def _number(row: Row, key: str) -> float:
    value = row[key]
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        raise ValueError(f"{key} is not an observed number")
    return float(value)


def _text(row: Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} is not text")
    return value


def build_schema() -> Row:
    """Return explicit Fabric-friendly types, grains, and edge-table mappings."""
    tables: list[Row] = []
    for name, (key, description, ontology, columns) in SPECS.items():
        declared = list(columns)
        if ontology:
            declared.append(_c("Synthetic", "Boolean", "True: generated fictional observation, never operator data."))
        tables.append(
            {
                "name": name,
                "key": key,
                "description": description,
                "ontology": ontology,
                "synonyms": [name, {"ProductionDay": "ProduksiHarian", "Well": "Sumur"}.get(name, f"Fiktif{name}")],
                "columns": [
                    {"name": n, "type": t, "description": d, "nullable": nullable} for n, t, d, nullable in declared
                ],
            }
        )
    edges = [
        ("well_belongs_to_field", "Well", "Field", "Well", "WellId", "FieldId", "ManyToOne"),
        ("reservoir_belongs_to_field", "Reservoir", "Field", "Reservoir", "ReservoirId", "FieldId", "ManyToOne"),
        ("wellbore_belongs_to_well", "Wellbore", "Well", "Wellbore", "WellboreId", "WellId", "ManyToOne"),
        (
            "wellbore_sidetracks_from_parent",
            "Wellbore",
            "Wellbore",
            "Wellbore",
            "WellboreId",
            "ParentWellboreId",
            "ManyToOne",
        ),
        ("completion_in_wellbore", "Completion", "Wellbore", "Completion", "CompletionId", "WellboreId", "ManyToOne"),
        (
            "completion_opens_reservoir",
            "Completion",
            "Reservoir",
            "Completion",
            "CompletionId",
            "ReservoirId",
            "ManyToOne",
        ),
        (
            "production_day_observes_well",
            "ProductionDay",
            "Well",
            "ProductionDay",
            "ProductionDayId",
            "WellId",
            "ManyToOne",
        ),
        ("well_test_measures_wellbore", "WellTest", "Wellbore", "WellTest", "WellTestId", "WellboreId", "ManyToOne"),
        (
            "well_test_supported_by_document",
            "WellTest",
            "Document",
            "WellTest",
            "WellTestId",
            "DocumentId",
            "ManyToOne",
        ),
        (
            "log_interval_in_wellbore",
            "LogInterval",
            "Wellbore",
            "LogInterval",
            "LogIntervalId",
            "WellboreId",
            "ManyToOne",
        ),
        (
            "log_interval_correlates_reservoir",
            "LogInterval",
            "Reservoir",
            "LogInterval",
            "LogIntervalId",
            "ReservoirId",
            "ManyToOne",
        ),
        (
            "log_interval_supported_by_document",
            "LogInterval",
            "Document",
            "LogInterval",
            "LogIntervalId",
            "DocumentId",
            "ManyToOne",
        ),
        ("fluid_sample_from_well", "FluidSample", "Well", "FluidSample", "FluidSampleId", "WellId", "ManyToOne"),
        (
            "fluid_sample_from_reservoir",
            "FluidSample",
            "Reservoir",
            "FluidSample",
            "FluidSampleId",
            "ReservoirId",
            "ManyToOne",
        ),
        (
            "fluid_report_supersedes_prior",
            "FluidSample",
            "FluidSample",
            "FluidSample",
            "FluidSampleId",
            "SupersedesFluidSampleId",
            "ManyToOne",
        ),
        (
            "fluid_sample_supported_by_document",
            "FluidSample",
            "Document",
            "FluidSample",
            "FluidSampleId",
            "DocumentId",
            "ManyToOne",
        ),
        (
            "operational_event_affects_well",
            "OperationalEvent",
            "Well",
            "OperationalEvent",
            "OperationalEventId",
            "WellId",
            "ManyToOne",
        ),
        (
            "operational_event_supported_by_document",
            "OperationalEvent",
            "Document",
            "OperationalEvent",
            "OperationalEventId",
            "DocumentId",
            "ManyToOne",
        ),
        ("document_covers_well", "Document", "Well", "DocumentWell", "DocumentId", "WellId", "ManyToMany"),
        (
            "document_supersedes_prior",
            "Document",
            "Document",
            "Document",
            "DocumentId",
            "SupersedesDocumentId",
            "ManyToOne",
        ),
        (
            "seismic_interpretation_of_reservoir",
            "SeismicInterpretation",
            "Reservoir",
            "SeismicInterpretation",
            "SeismicInterpretationId",
            "ReservoirId",
            "ManyToOne",
        ),
        (
            "seismic_interpretation_supported_by_document",
            "SeismicInterpretation",
            "Document",
            "SeismicInterpretation",
            "SeismicInterpretationId",
            "DocumentId",
            "ManyToOne",
        ),
    ]
    return {
        "version": VERSION,
        "asOf": AS_OF,
        "disclaimer": DISCLAIMER,
        "tables": tables,
        "relationships": [
            {
                "name": name,
                "source": source,
                "target": target,
                "table": table,
                "source_key": source_key,
                "target_key": target_key,
                "description": name.replace("_", " ") + "; join identifiers, never infer production allocation.",
                "cardinality": cardinality,
            }
            for name, source, target, table, source_key, target_key, cardinality in edges
        ],
    }


def _build_inventory(tables: Tables) -> None:
    fields = [
        ("CS", "Seroja Darat", "Central Sumatra", "Oil", "Onshore conventional oil"),
        ("SS", "Rimba Selatan", "South Sumatra", "GasCondensate", "Onshore retrograde gas-condensate"),
        ("JV", "Laut Lazuardi", "Northwest Java", "Oil", "Offshore oil with associated gas"),
    ]
    formations = {
        "CS": [("Menggala", 980.0), ("Bekasap", 1190.0)],
        "SS": [("Talang Akar A", 2100.0), ("Talang Akar B", 2460.0)],
        "JV": [("Upper Cibulakan A", 1340.0), ("Upper Cibulakan B", 1640.0)],
    }
    for code, name, basin, system, setting in fields:
        field_id = f"FIC-{code}"
        tables["Field"].append(
            _entity(FieldId=field_id, Name=name, Basin=basin, HydrocarbonSystem=system, Setting=setting)
        )
        for index, (formation, depth) in enumerate(formations[code], 1):
            tables["Reservoir"].append(
                _entity(
                    ReservoirId=f"R-{code}-{index}",
                    FieldId=field_id,
                    Name=f"{name} {formation}",
                    Formation=formation,
                    ReferenceTopTvdssM=depth,
                    PressureReferenceTvdssM=depth + 30.0,
                )
            )
        for index in range(1, 5):
            well = f"{field_id}-{index:02}"
            tables["Well"].append(
                _entity(
                    WellId=well,
                    FieldId=field_id,
                    Name=well,
                    ProductionSystem="GasCondensate" if code == "SS" else "OilAssociatedGas",
                    FirstProductionDate=_utc("2017-03-01" if code == "CS" else "2020-07-01"),
                )
            )
            md = {"CS": 1400.0, "SS": 2700.0, "JV": 2220.0}[code] + 100.0 * index
            tvd = {"CS": 1280.0, "SS": 2530.0, "JV": 1770.0}[code] + 40.0 * index
            elevation = {"CS": 35.0, "SS": 48.0, "JV": 25.0}[code]
            bore = f"{well}-M"
            tables["Wellbore"].append(
                _entity(
                    WellboreId=bore,
                    WellId=well,
                    ParentWellboreId=None,
                    BoreType="Main",
                    SurveyDate=_utc("2026-05-15"),
                    TotalDepthMdM=md,
                    TotalDepthTvdM=tvd,
                    ReferenceElevationM=elevation,
                    TotalDepthTvdssM=tvd - elevation,
                    DepthReference="Synthetic drill floor above MSL; no geographic survey supplied.",
                )
            )
            layer = 1 if index <= 2 else 2
            depth = formations[code][layer - 1][1]
            top_md = depth + elevation + {"CS": 80.0, "SS": 120.0, "JV": 220.0}[code] + index * 5.0
            end = {"FIC-CS-02": "2026-07-01", "FIC-JV-03": "2026-08-15"}.get(well)
            tables["Completion"].append(
                _entity(
                    CompletionId=f"C-{code}-{index:02}-M",
                    WellboreId=bore,
                    ReservoirId=f"R-{code}-{layer}",
                    EffectiveFrom=_utc("2026-05-01"),
                    EffectiveTo=_utc(end) if end else None,
                    TopMdM=top_md,
                    BaseMdM=top_md + 14.0,
                    FlowConfiguration="Commingled" if well == "FIC-CS-04" else "Single",
                    AllocationEvidence="No independent zonal meter or production logging survey.",
                )
            )
            tables["LogInterval"].append(
                _entity(
                    LogIntervalId=f"L-{code}-{index:02}-M",
                    WellboreId=bore,
                    ReservoirId=f"R-{code}-{layer}",
                    LogDate=_utc("2026-05-15"),
                    TopMdM=top_md - 2.0,
                    BaseMdM=top_md + 18.0,
                    TopTvdssM=depth - 2.0,
                    BaseTvdssM=depth + 15.0,
                    PorosityFraction=round({"CS": 0.25, "SS": 0.21, "JV": 0.23}[code] - 0.01 * index, 3),
                    PermeabilityMd={"CS": 360.0, "SS": 260.0, "JV": 220.0}[code] / index,
                    WaterSaturationFraction=0.42 + 0.04 * index,
                    ShaleVolumeFraction=0.12 + 0.02 * index,
                    FormationWaterSalinityPpm=18000.0 + 1000.0 * index,
                    ResistivityOhmM=12.0 - index,
                    Method="Synthetic density-neutron phi, shaly-sand Sw, analogue k transform; no core calibration.",
                    DocumentId="D-LOG",
                )
            )
    for well, md, tvd, effective in [
        ("FIC-CS-02", 1780.0, 1410.0, "2026-07-01"),
        ("FIC-JV-03", 3190.0, 1960.0, "2026-08-15"),
    ]:
        main = next(row for row in tables["Wellbore"] if row["WellboreId"] == f"{well}-M")
        completion = next(row for row in tables["Completion"] if row["WellboreId"] == f"{well}-M")
        tables["Wellbore"].append(
            {
                **main,
                "WellboreId": f"{well}-ST1",
                "ParentWellboreId": f"{well}-M",
                "BoreType": "Sidetrack",
                "SurveyDate": _utc(effective),
                "TotalDepthMdM": md,
                "TotalDepthTvdM": tvd,
                "TotalDepthTvdssM": tvd - _number(main, "ReferenceElevationM"),
            }
        )
        tables["Completion"].append(
            {
                **completion,
                "CompletionId": _text(completion, "CompletionId").replace("-M", "-ST1"),
                "WellboreId": f"{well}-ST1",
                "EffectiveFrom": _utc(effective),
                "EffectiveTo": None,
                "TopMdM": _number(completion, "TopMdM") + 170.0,
                "BaseMdM": _number(completion, "BaseMdM") + 170.0,
            }
        )
    tables["Completion"].append(
        _entity(
            CompletionId="C-CS-04-UPPER",
            WellboreId="FIC-CS-04-M",
            ReservoirId="R-CS-1",
            EffectiveFrom=_utc("2026-05-01"),
            EffectiveTo=None,
            TopMdM=1115.0,
            BaseMdM=1129.0,
            FlowConfiguration="Commingled",
            AllocationEvidence="No independent zonal meter or production logging survey.",
        )
    )
    for code, well_index, top_md, top_tvdss, porosity, permeability, sw, shale in [
        ("CS", 3, 1240.0, 1120.0, 0.20, 9.0, 0.62, 0.32),
        ("SS", 3, 2790.0, 2540.0, 0.18, 28.0, 0.58, 0.22),
        ("SS", 4, 2800.0, 2535.0, 0.13, 8.0, 0.74, 0.35),
    ]:
        tables["LogInterval"].append(
            _entity(
                LogIntervalId=f"L-{code}-{well_index:02}-UNPERF",
                WellboreId=f"FIC-{code}-{well_index:02}-M",
                ReservoirId=f"R-{code}-2",
                LogDate=_utc("2026-08-22"),
                TopMdM=top_md,
                BaseMdM=top_md + 12.0,
                TopTvdssM=top_tvdss,
                BaseTvdssM=top_tvdss + 10.0,
                PorosityFraction=porosity,
                PermeabilityMd=permeability,
                WaterSaturationFraction=sw,
                ShaleVolumeFraction=shale,
                FormationWaterSalinityPpm=23000.0,
                ResistivityOhmM=5.5,
                Method="Synthetic shaly-sand interpretation; k transform uncertain; "
                "no core/PVT/economic certification.",
                DocumentId="D-LOG",
            )
        )


def _build_production(tables: Tables) -> None:
    for well_row in tables["Well"]:
        well = _text(well_row, "WellId")
        code, index = well.split("-")[1], int(well[-2:])
        for offset in range(92):
            day = date(2026, 6, 1) + timedelta(days=offset)
            hours = 22.0 + ((offset + index) % 5) * 0.5
            cycling = 1.0 + 0.035 * math.sin((offset + index * 3) / 7.0)
            if code == "SS":
                gas_capacity = [9.5, 11.0, 6.8, 4.5][index - 1] * (1.0 - offset * 0.0006) * cycling
                if date(2026, 8, 12) <= day < date(2026, 8, 22):
                    hours, gas_capacity = 12.0, gas_capacity * 0.62
                gas = gas_capacity * hours / 24.0
                oil, water, condensate = 0.0, gas * (5.5 + index), gas * (8.0 + index * 1.8)
            else:
                if code == "CS":
                    liquid = [1500.0, 1250.0, 1050.0, 1650.0][index - 1] * cycling
                    watercut = [0.71, 0.76, 0.81, 0.83][index - 1] + offset * (0.0017 if index == 1 else 0.0003)
                    if index == 2 and day >= date(2026, 7, 1):
                        liquid *= 1.16
                        watercut -= 0.025
                else:
                    liquid = [1520.0, 1200.0, 1150.0, 1620.0][index - 1] * cycling
                    watercut = [0.53, 0.62, 0.57, 0.75][index - 1] + offset * 0.0005
                    if index == 3 and day >= date(2026, 8, 15):
                        liquid *= 1.13
                        watercut -= 0.015
                throughput = liquid * hours / 24.0
                oil, water = throughput * (1.0 - watercut), throughput * watercut
                gas, condensate = oil * (0.00075 if code == "CS" else 0.0012), 0.0
            shutdown = (
                (well == "FIC-CS-02" and date(2026, 6, 27) <= day < date(2026, 7, 1))
                or (well == "FIC-JV-01" and date(2026, 8, 5) <= day < date(2026, 8, 15))
                or (well == "FIC-JV-03" and date(2026, 8, 10) <= day < date(2026, 8, 15))
            )
            if shutdown:
                oil = water = gas = condensate = hours = 0.0
            missing = well == "FIC-JV-02" and day == date(2026, 8, 17)
            revised = well == "FIC-CS-04" and day.month == 7
            quality = (
                "Missing"
                if missing
                else "ShutdownValidated"
                if shutdown
                else "RevisedValidated"
                if revised
                else "Validated"
            )
            physical_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) - timedelta(hours=7)
            tables["ProductionDay"].append(
                _entity(
                    ProductionDayId=f"{well}-{day:%Y%m%d}",
                    WellId=well,
                    ProductionDate=_utc(day),
                    ProductionCalendar="Asia/Jakarta; WIB UTC+07; local midnight-to-midnight",
                    PeriodStartUtc=physical_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    PeriodEndUtc=(physical_start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    OilStb=None if missing else round(oil, 3),
                    WaterBbl=None if missing else round(water, 3),
                    GasMMscf=None if missing else round(gas, 6),
                    CondensateStb=None if missing else round(condensate, 3),
                    OperatingHours=None if missing else hours,
                    Quality=quality,
                    MeterBasis="Telemetry and manual backup unavailable"
                    if missing
                    else "Validated shutdown isolation record"
                    if shutdown
                    else "Reconciled well separator allocation; no reservoir split"
                    if revised
                    else "Integrated well separator measurement, standard volumes",
                    Revision=2 if revised else 1,
                    RecordedAt=_utc("2026-08-06")
                    if revised
                    else (physical_start + timedelta(days=1, hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )


def _build_events_and_tests(tables: Tables) -> None:
    events = [
        (
            "E-CS02-WO",
            "FIC-CS-02",
            "2026-06-27",
            "2026-07-01",
            "Workover",
            "Completed",
            "ApprovedExecution",
            "Well isolated for tubing repair and short sidetrack.",
            "Execution completed; differing choke, hours and depletion confound causal uplift.",
            "D-CS-WO",
        ),
        (
            "E-CS03-PROP",
            "FIC-CS-03",
            "2026-09-10",
            "2026-09-13",
            "Proposal",
            "Planned",
            "NotApproved",
            "Proposed stimulation; no scheduled downtime authorized.",
            "Shaly low-permeability interval requires laboratory, integrity and economics review.",
            "D-CS-PROP",
        ),
        (
            "E-JV01-SHUT",
            "FIC-JV-01",
            "2026-08-05",
            "2026-08-15",
            "Shutdown",
            "Completed",
            "ApprovedExecution",
            "Export flowline valve replacement with isolation.",
            "Ten validated zero-flow dates; attribution beyond recorded outage requires engineering review.",
            "D-JV-OPS",
        ),
        (
            "E-JV03-WO",
            "FIC-JV-03",
            "2026-08-10",
            "2026-08-15",
            "Workover",
            "Completed",
            "ApprovedExecution",
            "Tubing service and directional sidetrack tie-in.",
            "Completed scope only; before/after observations do not establish counterfactual workover uplift.",
            "D-JV-OPS",
        ),
    ]
    for index in range(1, 5):
        events.append(
            (
                f"E-SS{index:02}-COMP",
                f"FIC-SS-{index:02}",
                "2026-08-12",
                "2026-08-22",
                "CompressorCurtailment",
                "Completed",
                "ApprovedExecution",
                "Shared export compressor capacity restriction.",
                "Twelve onstream hours/day under reduced export capacity; test conditions differ.",
                "D-GAS-TEST",
            )
        )
    for event_id, well, start, end, kind, status, approval, reason, description, document in events:
        tables["OperationalEvent"].append(
            _entity(
                OperationalEventId=event_id,
                WellId=well,
                StartDate=_utc(start),
                EndDate=_utc(end),
                EventType=kind,
                Status=status,
                DowntimeReason=reason,
                ApprovalStatus=approval,
                Description=description,
                DocumentId=document,
            )
        )
    for well_row in tables["Well"]:
        well = _text(well_row, "WellId")
        gas_well = well.startswith("FIC-SS")
        index = int(well[-2:])
        gas = [9.8, 11.7, 7.1, 4.7][index - 1] if gas_well else 0.3
        oil = 0.0 if gas_well else 240.0 + index * 45.0
        tables["WellTest"].append(
            _entity(
                WellTestId=f"T-{well}-01",
                WellboreId=f"{well}-ST1" if well == "FIC-CS-02" else f"{well}-M",
                TestDate="2026-08-03T02:00:00Z" if well == "FIC-JV-01" else "2026-08-08T02:00:00Z",
                DurationHours=6.0 if gas_well else 4.0,
                Choke64thsIn=32 if gas_well else 24,
                Stabilized=gas_well or index != 3,
                OilRateStbPerDay=oil,
                WaterRateBblPerDay=gas * (5.5 + index) if gas_well else oil * 3.2,
                GasRateMMscfPerDay=gas,
                CondensateRateStbPerDay=gas * (8.0 + index * 1.8) if gas_well else 0.0,
                FlowingPressurePsia=1380.0 if gas_well else 260.0,
                PressureLocation="Surface test-separator inlet; gauge absolute; not reservoir static datum.",
                Conditions="Dedicated separator before export compressor restriction; stabilized criterion "
                "last 2 h within 3% for gas. " + STANDARD_CONDITIONS,
                DocumentId="D-GAS-TEST" if gas_well else "D-LOG",
            )
        )


def _build_fluids_and_seismic(tables: Tables) -> None:
    for index in range(1, 5):
        layer = 1 if index <= 2 else 2
        tables["FluidSample"].append(
            _entity(
                FluidSampleId=f"FS-SS-{index:02}-1",
                SampleGroupId=f"SAMPLE-SS-{index:02}-JUL30",
                WellId=f"FIC-SS-{index:02}",
                ReservoirId=f"R-SS-{layer}",
                SampleDate="2026-07-30T03:00:00Z",
                SampleLocation="Recombined separator gas and liquid sample; dry-gas CO2 by chromatography.",
                PressureMeasurementDate="2026-05-20T03:00:00Z",
                StaticPressurePsia=[3240.0, 3258.0, 3590.0, 3505.0][index - 1],
                PressureDatumTvdssM=[2100.0, 2145.0, 2460.0, 2480.0][index - 1],
                PressureGradientPsiPerM=0.42,
                ReservoirTemperatureC=112.0 + index * 2.0,
                GasCo2MolPercent=3.2 if index == 2 else 2.7 + index * 0.2,
                ReportDate=_utc("2026-08-01" if index == 2 else "2026-08-03"),
                ReportStatus="Superseded" if index == 2 else "Approved",
                SupersedesFluidSampleId=None,
                Provenance="Synthetic July recombined-fluid lab report paired with historical May static pressure "
                "after 36 h shut-in; uncertainty +/-15 psi; gradient is an engineering assumption.",
                DocumentId="D-CO2-OLD" if index == 2 else "D-SS-CONNECT",
            )
        )
    old = next(row for row in tables["FluidSample"] if row["WellId"] == "FIC-SS-02")
    tables["FluidSample"].extend(
        [
            {
                **old,
                "FluidSampleId": "FS-SS-02-2",
                "GasCo2MolPercent": 4.1,
                "ReportDate": _utc("2026-08-04"),
                "ReportStatus": "Approved",
                "SupersedesFluidSampleId": "FS-SS-02-1",
                "DocumentId": "D-CO2-CURRENT",
            },
            {
                **old,
                "FluidSampleId": "FS-SS-02-DRAFT",
                "GasCo2MolPercent": 5.6,
                "ReportDate": _utc("2026-08-29"),
                "ReportStatus": "Draft",
                "SupersedesFluidSampleId": None,
                "DocumentId": "D-CO2-DRAFT",
            },
        ]
    )
    for reservoir in tables["Reservoir"]:
        reservoir_id = _text(reservoir, "ReservoirId")
        top = _number(reservoir, "ReferenceTopTvdssM")
        for version in (1, 2):
            median = top + (25.0 if version == 1 else 0.0)
            tables["SeismicInterpretation"].append(
                _entity(
                    SeismicInterpretationId=f"SI-{reservoir_id}-V{version}",
                    ReservoirId=reservoir_id,
                    Version=version,
                    EffectiveDate=_utc("2026-06-01" if version == 1 else "2026-08-20"),
                    Status="Superseded" if version == 1 else "Current",
                    TopDepthQ10TvdssM=median - (90.0 if version == 1 else 65.0),
                    TopDepthQ50TvdssM=median,
                    TopDepthQ90TvdssM=median + (110.0 if version == 1 else 85.0),
                    EvidenceKind="ConceptualSynthetic; no acquired seismic or supported 3D rendering evidence.",
                    UncertaintyExplanation="Nonexceedance depth quantiles, positive downward: P(depth<=q)=p. "
                    "Not hydrocarbon volume P10/P90; no spatial covariance, fault seal, or connectivity proof.",
                    DocumentId="D-SEISMIC",
                )
            )


def _build_document_metadata(tables: Tables) -> None:
    all_wells = [_text(row, "WellId") for row in tables["Well"]]
    for document_id, title, status, day, supersedes, selectors in DOCUMENT_SPECS:
        tables["Document"].append(
            _entity(
                DocumentId=document_id,
                FileName=f"{document_id.lower()}.pdf",
                Title=title,
                Status=status,
                EffectiveDate=_utc(day),
                SupersedesDocumentId=supersedes,
            )
        )
        wells = [
            well for well in all_wells if "ALL" in selectors or well in selectors or well.split("-")[1] in selectors
        ]
        tables["DocumentWell"].extend(
            {
                "DocumentWellId": f"{document_id}-{well}",
                "DocumentId": document_id,
                "WellId": well,
            }
            for well in wells
        )


def build_tables() -> Tables:
    """Create raw observations only; neither evaluation questions nor expected answers enter these tables."""
    tables: Tables = {name: [] for name in SPECS}
    _build_inventory(tables)
    _build_production(tables)
    _build_events_and_tests(tables)
    _build_fluids_and_seismic(tables)
    _build_document_metadata(tables)
    validate_tables(tables)
    return tables


def _period_observations(tables: Tables, well: str, start: str, end: str) -> str:
    observations = [
        row
        for row in tables["ProductionDay"]
        if row["WellId"] == well and start <= _text(row, "ProductionDate")[:10] < end
    ]
    lines = [
        f"{_text(row, 'ProductionDate')[:10]} WIB: oil={row['OilStb']} STB; water={row['WaterBbl']} bbl; "
        f"gas={row['GasMMscf']} MMscf; condensate={row['CondensateStb']} STB; "
        f"onstream={row['OperatingHours']} h; quality={row['Quality']}."
        for row in observations
    ]
    return "\n".join(lines)


def build_documents(tables: Tables) -> list[Row]:
    """Self-authored evidence, not questions or an embedded answer key."""
    current_july_oil = round(
        sum(
            _number(row, "OilStb")
            for row in tables["ProductionDay"]
            if row["WellId"] == "FIC-CS-04" and _text(row, "ProductionDate").startswith("2026-07")
        ),
        3,
    )
    narratives: dict[str, list[tuple[str, str]]] = {
        "D-BASE": [
            ("Ruang lingkup", DISCLAIMER + " Dua belas sumur, tiga lapangan; periode 1 Juni-31 Agustus 2026."),
            (
                "Basis kalender dan pengukuran",
                "Tanggal produksi memakai hari kalender WIB (Asia/Jakarta), 00:00-24:00. ProductionDate adalah "
                "label tanggal pada 00:00Z; bukan awal periode fisik. PeriodStartUtc adalah pukul 17:00Z hari "
                "sebelumnya. Volume harian terintegrasi berbeda dari laju uji. " + STANDARD_CONDITIONS,
            ),
            (
                "Kelengkapan dan batasan",
                "Missing berarti nilai tidak tersedia, bukan nol. ShutdownValidated mencatat nol dan 0 jam operasi. "
                "OilStb tidak termasuk condensate. Watercut adalah water/(oil+water), dihitung dari jumlah volume "
                "untuk agregasi, tidak didefinisikan saat tidak ada aliran. Gas-condensate menggunakan WGR dan CGR, "
                "bukan watercut. Jangan menggandakan volume melalui completion yang commingled. Tidak ada cadangan, "
                "biaya, persetujuan keselamatan baru, atau model kontra-faktual dalam paket ini.",
            ),
            ("Konteks publik, bukan data aset", "\n".join(f"{s['context']} {s['url']}" for s in SOURCES)),
        ],
        "D-CS-WO": [
            (
                "Pelaksanaan",
                "FIC-CS-02 diisolasi 27-30 Juni. Perbaikan tubing dan sidetrack pendek selesai; completion utama "
                "berakhir eksklusif 1 Juli dan completion ST1 berlaku mulai 1 Juli. Persetujuan berlaku pada "
                "lingkup historis itu saja. Tekanan, choke dan jam operasi bukan kondisi terkontrol identik.",
            ),
            ("Catatan sebelum", _period_observations(tables, "FIC-CS-02", "2026-06-20", "2026-06-27")),
            ("Catatan setelah", _period_observations(tables, "FIC-CS-02", "2026-07-02", "2026-07-09")),
            (
                "Interpretasi terbatas",
                "Perubahan teramati tidak membuktikan uplift kausal workover; tidak tersedia baseline kontra-faktual, "
                "matched control atau analisis ketidakpastian. Tidak ada klaim persentase uplift yang disetujui.",
            ),
        ],
        "D-CS-PROP": [
            (
                "Usulan belum disetujui",
                "FIC-CS-03: usulan stimulasi 10-12 September berstatus NotApproved; bukan pekerjaan yang sudah "
                "dilakukan. Interpretasi shaly sand bukan jaminan respons stimulasi. Kelayakan memerlukan core, "
                "uji kompatibilitas, integritas sumur, barrier review, HAZOP, izin operasi dan ekonomi.",
            ),
            ("Interval dasar usulan", "L-CS-03-UNPERF adalah interval log terpisah tanpa overlap perforasi aktif."),
        ],
        "D-CS-COMM": [
            (
                "Konfigurasi dua reservoir",
                "FIC-CS-04 memiliki dua completion aktif di R-CS-1 dan R-CS-2. Aliran dicampur sebelum separator "
                "sumur. Tidak tersedia PLT, tracer, selective test atau meter zona. Volume ProductionDay adalah "
                "total sumur; pembagian 50:50, proporsional ketebalan, atau seluruh volume per zona tidak sah.",
            ),
        ],
        "D-ALLOC-OLD": [
            (
                "Laporan historis yang telah digantikan",
                f"Total oil CS04 Juli tercatat {current_july_oil + 750.0:.3f} STB pada versi 1. "
                "Faktor alokasi separator lama kemudian ditemukan tidak sesuai rekonsiliasi meter. "
                "Status SUPERSEDED oleh D-ALLOC-CURRENT; angka ini jangan digabungkan ke volume kanonik.",
            ),
        ],
        "D-ALLOC-CURRENT": [
            (
                "Rekonsiliasi yang disetujui",
                f"Total oil CS04 Juli setelah koreksi adalah {current_july_oil:.3f} STB. "
                "Versi 2 disetujui 6 Agustus dan menggantikan D-ALLOC-OLD. Seluruh 31 baris Juli memiliki Revision=2 "
                "dan Quality=RevisedValidated. Ada tepat satu baris kanonik per sumur per hari. "
                "Rekonsiliasi berlaku pada total sumur, bukan pemisahan produksi antar reservoir.",
            ),
        ],
        "D-GAS-TEST": [
            (
                "Uji vs operasi",
                "Uji 8 Agustus menggunakan separator khusus sebelum pembatasan kompresor ekspor. Laju dinormalisasi "
                "24 jam dari uji 6 jam dan bukan volume produksi harian. Pada 12-21 Agustus semua sumur Rimba "
                "beroperasi 12 jam/hari pada kapasitas ekspor berkurang. Selisih dari laju uji tidak otomatis error. "
                "Air dan kondensat dilaporkan sebagai WGR/CGR per MMscf, tidak digabungkan dengan minyak.",
            ),
            (
                "Catatan uji",
                "\n".join(
                    f"{row['WellTestId']}: start={row['TestDate']}; "
                    f"{_number(row, 'GasRateMMscfPerDay'):.3f} MMscf/day; "
                    f"{_number(row, 'CondensateRateStbPerDay'):.3f} STB condensate/day; "
                    f"duration={row['DurationHours']} h; "
                    f"choke={row['Choke64thsIn']}/64 in; stabilized={row['Stabilized']}; "
                    f"flowing separator pressure={row['FlowingPressurePsia']} psia."
                    for row in tables["WellTest"]
                    if row["DocumentId"] == "D-GAS-TEST"
                ),
            ),
            ("Observasi saat pembatasan SS02", _period_observations(tables, "FIC-SS-02", "2026-08-15", "2026-08-16")),
        ],
        "D-SS-CONNECT": [
            (
                "Hipotesis komunikasi, bukan kesimpulan final",
                "R-SS-1 dikorelasikan secara stratigrafi antara SS01 dan SS02, tetapi komunikasi hidraulik belum "
                "dibuktikan. Tekanan shut-in historis 20 Mei (bukan tekanan saat sampling fluida 30 Juli) pada "
                "datum berbeda harus dinormalisasi: P_ref=P_obs+gradient*"
                "(reference_TVDSS-observed_TVDSS). Gradien 0.42 psi/m adalah asumsi fluida sintetis. "
                "Kesamaan tekanan dalam ketidakpastian +/-15 psi tidak cukup tanpa interference test, sejarah "
                "tekanan dan PVT. R-SS-2 dapat memiliki kompartemen; tidak tersedia fault-seal calibration.",
            ),
            (
                "Observasi tekanan",
                "\n".join(
                    f"{row['WellId']}: {row['StaticPressurePsia']} psia pada {row['PressureDatumTvdssM']} m TVDSS; "
                    f"pressure measured={row['PressureMeasurementDate']}; fluid sample={row['SampleDate']}; "
                    f"shut-in 36 h; temperature={row['ReservoirTemperatureC']} degC."
                    for row in tables["FluidSample"]
                    if row["ReportStatus"] == "Approved"
                ),
            ),
        ],
        "D-LOG": [
            (
                "Batas interpretasi petrofisika",
                "MD diukur sepanjang lubang dari drill floor; TVDSS adalah kedalaman vertikal di bawah muka laut "
                "dan tidak dapat dipertukarkan dengan MD. Ketebalan gross interval bukan net pay. "
                "Screening Talang Akar phi>0.14, k>10 mD, Vsh<0.30, Sw<0.70, salinitas>10000 ppm berasal dari "
                "studi tertentu, bukan cutoff universal. Lolos screening tidak membuktikan cadangan, deliverability "
                "atau kelayakan perforasi. Overlap interval harus diperiksa terhadap completion efektif.",
            ),
            (
                "Interval tambahan belum diperforasi",
                "\n".join(
                    f"{row['LogIntervalId']}: MD {row['TopMdM']}-{row['BaseMdM']} m; "
                    f"TVDSS {row['TopTvdssM']}-{row['BaseTvdssM']} m; phi={row['PorosityFraction']}; "
                    f"k={row['PermeabilityMd']} mD; Sw={row['WaterSaturationFraction']}; "
                    f"Vsh={row['ShaleVolumeFraction']}; salinity={row['FormationWaterSalinityPpm']} ppm."
                    for row in tables["LogInterval"]
                    if "UNPERF" in _text(row, "LogIntervalId")
                ),
            ),
            (
                "Uji minyak",
                "\n".join(
                    f"{row['WellTestId']}: start={row['TestDate']}; oil={row['OilRateStbPerDay']} STB/day; "
                    f"water={row['WaterRateBblPerDay']} bbl/day; gas={row['GasRateMMscfPerDay']} MMscf/day; "
                    f"duration={row['DurationHours']} h; stabilized={row['Stabilized']}."
                    for row in tables["WellTest"]
                    if row["DocumentId"] == "D-LOG"
                ),
            ),
        ],
        "D-JV-OPS": [
            (
                "Catatan operasi",
                "JV01 berhenti 5-14 Agustus untuk penggantian valve flowline ekspor; volume dan jam nol divalidasi "
                "dari catatan isolasi. JV03 workover tubing dan tie-in sidetrack 10-14 Agustus selesai; ST1 efektif "
                "15 Agustus. Persetujuan historis pekerjaan bukan persetujuan keselamatan untuk usulan baru.",
            ),
            ("JV03 sebelum", _period_observations(tables, "FIC-JV-03", "2026-08-03", "2026-08-10")),
            ("JV03 setelah", _period_observations(tables, "FIC-JV-03", "2026-08-16", "2026-08-23")),
            (
                "Batas atribusi",
                "Produksi dipengaruhi watercut, jam operasi, choke dan kendala ekspor. Catatan sebelum/sesudah "
                "tidak cukup untuk persentase uplift kausal yang tersertifikasi.",
            ),
        ],
        "D-MISSING": [
            (
                "Insiden kelengkapan",
                "FIC-JV-02 tanggal 17 Agustus WIB kehilangan telemetri dan backup manual. OilStb, WaterBbl, "
                "GasMMscf, CondensateStb dan OperatingHours semuanya null dengan Quality=Missing. "
                "Belum ada rekonstruksi disetujui hingga as-of 1 September. Hari ini bukan shutdown dan bukan nol. "
                "Angka bulan Agustus harus menyebut cakupan; jangan mengisi rata-rata tetangga sebagai observasi.",
            ),
        ],
        "D-SEISMIC": [
            (
                "Bukan data seismik akuisisi",
                "Interpretasi versi 1 dan 2 adalah ilustrasi sintetis puncak reservoir. Tidak ada volume seismik "
                "3D, inline/crossline, velocity model, koordinat survei atau spatial covariance. Tidak dapat "
                "menghasilkan tampilan 3D struktur terverifikasi atau menghitung volume hidrokarbon dari angka ini.",
            ),
            (
                "Semantik kuantil",
                "Kedalaman TVDSS positif ke bawah; q10<=q50<=q90 memakai peluang nonexceedance P(depth<=q)=p. "
                "Ini bukan P10/P90 cadangan. Versi 2 menggantikan versi 1; perubahan ilustratif bukan hasil "
                "reprocessing seismik aktual. Kesamaan top depth tidak membuktikan komunikasi antar sumur.",
            ),
        ],
    }
    for document_id in ("D-CO2-OLD", "D-CO2-CURRENT", "D-CO2-DRAFT"):
        sample = next(row for row in tables["FluidSample"] if row["DocumentId"] == document_id)
        narratives[document_id] = [
            (
                "Hasil analisis versi ini",
                f"Physical sample {sample['SampleGroupId']} diambil {sample['SampleDate']}; "
                f"dry gas CO2={sample['GasCo2MolPercent']} mol%; report={sample['ReportDate']}; "
                f"status={sample['ReportStatus']}. Metode kromatografi gas pada sampel separator direkombinasi.",
            ),
            (
                "Kontrol revisi",
                "Versi 1 diganti karena koreksi kalibrasi kromatograf. D-CO2-CURRENT disetujui 4 Agustus. "
                "Pengukuran ulang pada D-CO2-DRAFT belum selesai QA dan tidak menggantikan hasil yang disetujui. "
                "Jangan memilih angka terbesar atau tanggal terbaru tanpa status; tidak tersedia spesifikasi "
                "material, corrosion envelope atau dasar untuk menyatakan operasi aman.",
            ),
        ]
    return [
        {
            "documentId": document["DocumentId"],
            "fileName": document["FileName"],
            "title": document["Title"],
            "status": document["Status"],
            "effectiveDate": document["EffectiveDate"],
            "supersedesDocumentId": document["SupersedesDocumentId"],
            "wellIds": [row["WellId"] for row in tables["DocumentWell"] if row["DocumentId"] == document["DocumentId"]],
            "sections": [
                {"heading": heading, "text": text} for heading, text in narratives[_text(document, "DocumentId")]
            ],
        }
        for document in tables["Document"]
    ]


def _schema_tables() -> list[Row]:
    return cast(list[Row], build_schema()["tables"])


def _columns(spec: Row) -> list[Row]:
    return cast(list[Row], spec["columns"])


def validate_tables(tables: Tables) -> None:
    """Reject schema drift, dangling graph identifiers, invalid datums, and missing/zero confusion."""
    if set(tables) != set(SPECS):
        raise ValueError("Table inventory differs from declared schema")
    keys: dict[str, set[str]] = {}
    for spec in _schema_tables():
        name, key = _text(spec, "name"), _text(spec, "key")
        columns = _columns(spec)
        seen: set[str] = set()
        for row in tables[name]:
            if set(row) != {_text(column, "name") for column in columns}:
                raise ValueError(f"{name}: row columns differ from schema")
            identifier = _text(row, key)
            if not identifier or identifier in seen:
                raise ValueError(f"{name}: duplicate or empty key")
            seen.add(identifier)
            for column in columns:
                value, kind = row[_text(column, "name")], _text(column, "type")
                if value is None:
                    if not column["nullable"]:
                        raise ValueError(f"{name}: unexpected null {_text(column, 'name')}")
                    continue
                valid = (
                    (kind in ("String", "DateTime") and isinstance(value, str))
                    or (kind == "Boolean" and isinstance(value, bool))
                    or (kind == "BigInt" and isinstance(value, int) and not isinstance(value, bool))
                    or (kind == "Double" and isinstance(value, (int, float)) and not isinstance(value, bool))
                )
                if not valid:
                    raise ValueError(f"{name}: wrong type for {column['name']}")
                if kind == "Double" and not math.isfinite(_number(row, _text(column, "name"))):
                    raise ValueError(f"{name}: nonfinite number")
                if kind == "DateTime":
                    text = _text(row, _text(column, "name"))
                    if not text.endswith("Z"):
                        raise ValueError("All graph timestamps must explicitly be UTC")
                    datetime.fromisoformat(text)
        keys[name] = seen
    for edge in cast(list[Row], build_schema()["relationships"]):
        for row in tables[_text(edge, "table")]:
            for endpoint, column in (("source", "source_key"), ("target", "target_key")):
                identifier = row[_text(edge, column)]
                if identifier is not None and identifier not in keys[_text(edge, endpoint)]:
                    raise ValueError(f"{edge['name']}: dangling identifier {identifier}")
    for row in tables["Wellbore"]:
        if _number(row, "TotalDepthMdM") < _number(row, "TotalDepthTvdM"):
            raise ValueError("Measured depth cannot be less than vertical depth")
        if not math.isclose(
            _number(row, "TotalDepthTvdssM"),
            _number(row, "TotalDepthTvdM") - _number(row, "ReferenceElevationM"),
            abs_tol=1e-9,
        ):
            raise ValueError("Inconsistent TVDSS datum")
    bores = {_text(row, "WellboreId"): row for row in tables["Wellbore"]}
    for name in ("Completion", "LogInterval"):
        for row in tables[name]:
            bore = bores[_text(row, "WellboreId")]
            if not (0 <= _number(row, "TopMdM") < _number(row, "BaseMdM") <= _number(bore, "TotalDepthMdM")):
                raise ValueError(f"{name}: interval is outside measured borehole depth")
            if name == "LogInterval":
                elevation = _number(bore, "ReferenceElevationM")
                if not (
                    _number(row, "TopTvdssM") < _number(row, "BaseTvdssM") <= _number(bore, "TotalDepthTvdssM")
                    and _number(row, "TopTvdssM") + elevation <= _number(row, "TopMdM")
                    and _number(row, "BaseTvdssM") + elevation <= _number(row, "BaseMdM")
                ):
                    raise ValueError("Log interval MD/TVDSS or total depth is inconsistent")
                for property_name in ("PorosityFraction", "WaterSaturationFraction", "ShaleVolumeFraction"):
                    if not 0 <= _number(row, property_name) <= 1:
                        raise ValueError(f"Invalid log fraction {property_name}")
            elif row["EffectiveTo"] is not None and _text(row, "EffectiveTo") <= _text(row, "EffectiveFrom"):
                raise ValueError("Completion effective range is not half-open and increasing")
    for bore_id, bore in bores.items():
        depth_points = [
            (0.0, -_number(bore, "ReferenceElevationM")),
            (_number(bore, "TotalDepthMdM"), _number(bore, "TotalDepthTvdssM")),
        ]
        for interval in tables["LogInterval"]:
            if interval["WellboreId"] == bore_id:
                depth_points.extend(
                    [
                        (_number(interval, "TopMdM"), _number(interval, "TopTvdssM")),
                        (_number(interval, "BaseMdM"), _number(interval, "BaseTvdssM")),
                    ]
                )
        depth_points.sort()
        for (previous_md, previous_tvdss), (md, tvdss) in pairwise(depth_points):
            if abs(tvdss - previous_tvdss) > md - previous_md + 1e-9:
                raise ValueError(f"{bore_id}: consecutive vertical depth change exceeds measured depth increment")
    seen_days: set[tuple[str, str]] = set()
    for row in tables["ProductionDay"]:
        pair = _text(row, "WellId"), _text(row, "ProductionDate")
        if pair in seen_days:
            raise ValueError("Duplicate canonical well/date")
        seen_days.add(pair)
        physical_start = datetime.fromisoformat(_text(row, "PeriodStartUtc"))
        physical_end = datetime.fromisoformat(_text(row, "PeriodEndUtc"))
        label = datetime.fromisoformat(_text(row, "ProductionDate"))
        if physical_end - physical_start != timedelta(days=1) or physical_start + timedelta(hours=7) != label:
            raise ValueError("WIB production calendar and UTC interval disagree")
        if _text(row, "RecordedAt") > AS_OF or datetime.fromisoformat(_text(row, "RecordedAt")) < physical_end:
            raise ValueError("Daily finalized timestamp outside observation/as-of bounds")
        quantities = ("OilStb", "WaterBbl", "GasMMscf", "CondensateStb", "OperatingHours")
        if row["Quality"] == "Missing":
            if any(row[name] is not None for name in quantities):
                raise ValueError("Missing days must retain null quantities")
        else:
            if any(_number(row, name) < 0.0 for name in quantities) or _number(row, "OperatingHours") > 24:
                raise ValueError("Invalid daily volume or operating hours")
            if _number(row, "OperatingHours") == 0.0 and any(_number(row, name) != 0.0 for name in quantities):
                raise ValueError("Zero operating hours require zero observed volumes")
            if row["Quality"] == "ShutdownValidated" and any(_number(row, name) != 0.0 for name in quantities):
                raise ValueError("Validated shutdown must have zero volumes and hours")
    test_rates = ("OilRateStbPerDay", "WaterRateBblPerDay", "GasRateMMscfPerDay", "CondensateRateStbPerDay")
    for test in tables["WellTest"]:
        if _number(test, "DurationHours") <= 0 or any(_number(test, rate) < 0 for rate in test_rates):
            raise ValueError("Well test duration must be positive and rates nonnegative")
        if not any(_number(test, rate) > 0 for rate in test_rates):
            continue
        well_id = bores[_text(test, "WellboreId")]["WellId"]
        test_start = datetime.fromisoformat(_text(test, "TestDate"))
        test_end = test_start + timedelta(hours=_number(test, "DurationHours"))
        for day in tables["ProductionDay"]:
            if day["WellId"] != well_id or day["OperatingHours"] != 0:
                continue
            day_start = datetime.fromisoformat(_text(day, "PeriodStartUtc"))
            day_end = datetime.fromisoformat(_text(day, "PeriodEndUtc"))
            if test_start < day_end and test_end > day_start:
                raise ValueError(f"{test['WellTestId']}: flowing test overlaps a zero-hour production interval")
    for row in tables["SeismicInterpretation"]:
        if not (
            _number(row, "TopDepthQ10TvdssM") <= _number(row, "TopDepthQ50TvdssM") <= _number(row, "TopDepthQ90TvdssM")
        ):
            raise ValueError("Invalid depth nonexceedance quantiles")


def _csv_text(name: str, rows: list[Row]) -> str:
    spec = next(spec for spec in _schema_tables() if spec["name"] == name)
    names = [_text(column, "name") for column in _columns(spec)]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: "" if value is None else str(value).lower() if isinstance(value, bool) else value
                for key, value in row.items()
            }
        )
    return stream.getvalue()


def _load_csv(connection: sqlite3.Connection, csv_tables: dict[str, str]) -> None:
    """The independent oracle consumes the exported CSV representation, never a precomputed ranking."""
    sql_types = {"String": "TEXT", "DateTime": "TEXT", "Double": "REAL", "BigInt": "INTEGER", "Boolean": "INTEGER"}
    relationships = cast(list[Row], build_schema()["relationships"])
    connection.execute("PRAGMA foreign_keys=ON")
    for spec in _schema_tables():
        name, key = _text(spec, "name"), _text(spec, "key")
        definitions: list[str] = []
        for column in _columns(spec):
            column_name = _text(column, "name")
            definition = f'"{column_name}" {sql_types[_text(column, "type")]}'
            definition += " PRIMARY KEY" if column_name == key else ""
            definition += "" if column["nullable"] else " NOT NULL"
            definitions.append(definition)
        references: set[tuple[str, str]] = set()
        for relationship in relationships:
            if relationship["table"] != name:
                continue
            for endpoint, column in (("source", "source_key"), ("target", "target_key")):
                target, local = _text(relationship, endpoint), _text(relationship, column)
                if local != key or target != name:
                    references.add((local, target))
        for local, target in sorted(references):
            target_key = SPECS[target][0]
            definitions.append(
                f'FOREIGN KEY ("{local}") REFERENCES "{target}"("{target_key}") DEFERRABLE INITIALLY DEFERRED'
            )
        if name == "ProductionDay":
            definitions.append('UNIQUE ("WellId", "ProductionDate")')
        if name == "DocumentWell":
            definitions.append('UNIQUE ("DocumentId", "WellId")')
        connection.execute(f'CREATE TABLE "{name}" ({", ".join(definitions)})')
    with connection:
        for spec in _schema_tables():
            name = _text(spec, "name")
            columns = _columns(spec)
            placeholders = ", ".join("?" for _ in columns)
            for raw in csv.DictReader(io.StringIO(csv_tables[name])):
                values: list[object] = []
                for column in columns:
                    value, kind = raw[_text(column, "name")], _text(column, "type")
                    values.append(
                        None
                        if value == ""
                        else float(value)
                        if kind == "Double"
                        else int(value)
                        if kind == "BigInt"
                        else int(value == "true")
                        if kind == "Boolean"
                        else value
                    )
                connection.execute(f'INSERT INTO "{name}" VALUES ({placeholders})', values)  # noqa: S608
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("CSV-loaded oracle has invalid foreign keys")


def _technical_scenario_specs() -> list[Row]:
    """Original operator-only regression prompts; never the primary end-user question handout."""
    return [
        {
            "id": "monthly-volume-rates-coverage",
            "question": "Bandingkan Juli dan Agustus untuk semua 12 sumur: volume minyak/kondensat/gas, "
            "laju per hari kalender dan laju minyak per hari onstream. Apa dampak data hilang?",
            "requiredTools": ["sql", "documents"],
            "sql": """
                SELECT w.WellId, substr(p.ProductionDate,1,7) AS Month,
                       COUNT(*) AS CalendarDays, COUNT(p.OilStb) AS ObservedDays,
                       SUM(CASE WHEN p.Quality='Missing' THEN 1 ELSE 0 END) AS MissingDays,
                       ROUND(SUM(p.OilStb),3) AS ObservedOilStb,
                       ROUND(SUM(p.CondensateStb),3) AS ObservedCondensateStb,
                       ROUND(SUM(p.GasMMscf),6) AS ObservedGasMMscf,
                       ROUND(SUM(p.OperatingHours),3) AS ObservedOperatingHours,
                       ROUND(SUM(p.OilStb)/COUNT(*),6) AS ObservedOilPerCalendarDay,
                       ROUND(24.0*SUM(p.OilStb)/NULLIF(SUM(p.OperatingHours),0),6) AS OilPerOnstreamDay
                FROM Well w JOIN ProductionDay p ON p.WellId=w.WellId
                WHERE p.ProductionDate>='2026-07-01' AND p.ProductionDate<'2026-09-01'
                GROUP BY w.WellId, substr(p.ProductionDate,1,7) ORDER BY w.WellId, Month
            """,
            "expectedBehavior": "Include all 12 wells and both months. Calendar denominator is 31 for each month "
            "even with missing data; label JV02 August volumes and calendar rate incomplete, not a true full-month "
            "total. Onstream-day rate is observed volume/(observed hours/24), not average test rate. "
            "Do not merge oil and condensate or treat gas MMscf as Mscf.",
            "requiredDocumentIds": ["D-BASE", "D-MISSING"],
        },
        {
            "id": "volume-weighted-watercut",
            "question": "Berapa watercut tertimbang volume Seroja Darat pada Juni, Juli, Agustus, dan WGR/CGR "
            "Rimba Selatan? Apakah rerata watercut harian boleh dipakai?",
            "requiredTools": ["sql", "documents"],
            "sql": """
                SELECT f.Name AS Field, substr(p.ProductionDate,1,7) AS Month,
                       COUNT(*) AS ExpectedWellDays, COUNT(p.OilStb) AS ObservedWellDays,
                       CASE WHEN f.HydrocarbonSystem='Oil'
                            THEN ROUND(SUM(p.WaterBbl)/NULLIF(SUM(p.OilStb+p.WaterBbl),0),6) END AS WeightedWatercut,
                       CASE WHEN f.HydrocarbonSystem='Oil'
                            THEN ROUND(AVG(p.WaterBbl/NULLIF(p.OilStb+p.WaterBbl,0)),6) END AS NaiveMeanWatercut,
                       CASE WHEN f.HydrocarbonSystem='GasCondensate'
                            THEN ROUND(SUM(p.WaterBbl)/NULLIF(SUM(p.GasMMscf),0),6) END AS WgrBblPerMMscf,
                       CASE WHEN f.HydrocarbonSystem='GasCondensate'
                            THEN ROUND(SUM(p.CondensateStb)/NULLIF(SUM(p.GasMMscf),0),6) END AS CgrStbPerMMscf
                FROM ProductionDay p JOIN Well w ON w.WellId=p.WellId JOIN Field f ON f.FieldId=w.FieldId
                WHERE f.FieldId IN ('FIC-CS','FIC-SS')
                GROUP BY f.FieldId, substr(p.ProductionDate,1,7) ORDER BY Field, Month
            """,
            "expectedBehavior": "Use ratio of summed volumes, not arithmetic mean of daily ratios. "
            "No-flow daily watercut is undefined. Gas-condensate uses WGR/CGR, not 100% watercut.",
            "requiredDocumentIds": ["D-BASE"],
        },
        {
            "id": "oil-decline-and-downtime",
            "question": "Sumur mana mengalami penurunan volume minyak Juli ke Agustus terbesar di antara "
            "sumur dengan data lengkap? Kaitkan dengan downtime tanpa menyatakan seluruh penurunan pasti kausal.",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                WITH m AS (
                    SELECT w.WellId,
                      SUM(CASE WHEN p.ProductionDate<'2026-08-01' THEN p.OilStb ELSE 0 END) AS JulyOil,
                      SUM(CASE WHEN p.ProductionDate>='2026-08-01' THEN p.OilStb ELSE 0 END) AS AugustOil,
                      SUM(CASE WHEN p.Quality='Missing' THEN 1 ELSE 0 END) AS MissingDays
                    FROM Well w JOIN ProductionDay p ON p.WellId=w.WellId
                    WHERE w.ProductionSystem='OilAssociatedGas'
                      AND p.ProductionDate>='2026-07-01' AND p.ProductionDate<'2026-09-01'
                    GROUP BY w.WellId
                ), ranked AS (
                    SELECT *, ROW_NUMBER() OVER (ORDER BY AugustOil-JulyOil,WellId) AS Position
                    FROM m WHERE MissingDays=0
                )
                SELECT r.WellId, ROUND(r.JulyOil,3) AS JulyOilStb, ROUND(r.AugustOil,3) AS AugustOilStb,
                  ROUND(r.AugustOil-r.JulyOil,3) AS OilChangeStb,
                  e.OperationalEventId,e.EventType,e.StartDate,e.EndDate,e.DowntimeReason,e.DocumentId
                FROM ranked r LEFT JOIN OperationalEvent e ON e.WellId=r.WellId
                  AND e.StartDate<'2026-09-01' AND e.EndDate>'2026-08-01' AND e.Status='Completed'
                WHERE r.Position=1 ORDER BY e.OperationalEventId
            """,
            "expectedBehavior": "Exclude incomplete JV02 from precise ranking and disclose exclusion. "
            "Link recorded outage to decline cautiously, not proof that all variance was caused by downtime.",
            "requiredDocumentIds": ["D-JV-OPS", "D-MISSING"],
        },
        {
            "id": "gas-test-versus-curtailment",
            "question": "Uji gas SS02 tinggi tetapi produksi 15 Agustus rendah. Apakah data salah? Bandingkan "
            "durasi, choke, kondisi uji, volume dan jam operasi.",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                SELECT w.WellId,t.WellTestId,t.TestDate,t.DurationHours,t.Choke64thsIn,t.Stabilized,
                    t.GasRateMMscfPerDay,t.FlowingPressurePsia,t.PressureLocation,
                    p.ProductionDate,p.GasMMscf,p.OperatingHours,
                    ROUND(24.0*p.GasMMscf/NULLIF(p.OperatingHours,0),6) AS ProductionOnstreamMMscfPerDay,
                    e.EventType,e.DowntimeReason
                FROM Well w JOIN Wellbore b ON b.WellId=w.WellId JOIN WellTest t ON t.WellboreId=b.WellboreId
                JOIN ProductionDay p ON p.WellId=w.WellId JOIN OperationalEvent e ON e.WellId=w.WellId
                WHERE w.WellId='FIC-SS-02' AND p.ProductionDate='2026-08-15T00:00:00Z'
                  AND p.ProductionDate>=e.StartDate AND p.ProductionDate<e.EndDate ORDER BY t.WellTestId
            """,
            "expectedBehavior": "Test is normalized rate from 6 h; daily is integrated volume under export "
            "curtailment. Explain valid differing conditions without asserting an error or substituting test volume.",
            "requiredDocumentIds": ["D-GAS-TEST"],
        },
        {
            "id": "depth-datums-and-sidetrack",
            "question": "Borehole terdalam menurut MD dan menurut TVD berbeda? Sertakan sidetrack, elevasi "
            "referensi dan TVDSS, serta jelaskan batasan model 3D.",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                SELECT WellboreId,WellId,BoreType,ParentWellboreId,TotalDepthMdM,TotalDepthTvdM,
                       ReferenceElevationM,TotalDepthTvdssM
                FROM Wellbore WHERE TotalDepthMdM=(SELECT MAX(TotalDepthMdM) FROM Wellbore)
                    OR TotalDepthTvdM=(SELECT MAX(TotalDepthTvdM) FROM Wellbore)
                ORDER BY TotalDepthMdM DESC,WellboreId
            """,
            "expectedBehavior": "MD is along-hole; TVD vertical below drill-floor; TVDSS=TVD-elevation. "
            "Identify differing maxima and parent sidetrack. Refuse a verified 3D seismic rendering: only "
            "conceptual versioned nonexceedance depth quantiles exist, not acquired 3D data.",
            "requiredDocumentIds": ["D-SEISMIC"],
        },
        {
            "id": "uncompleted-log-screening",
            "question": "Interval Talang Akar mana belum diperforasi pada 31 Agustus dan lolos screening "
            "phi>14%, k>10 mD, Vsh<30%, Sw<70%, salinitas>10000 ppm? Apakah ini cadangan terbukti?",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                SELECT l.LogIntervalId,b.WellId,l.ReservoirId,l.TopMdM,l.BaseMdM,l.TopTvdssM,l.BaseTvdssM,
                  l.PorosityFraction,l.PermeabilityMd,l.ShaleVolumeFraction,l.WaterSaturationFraction,
                  l.FormationWaterSalinityPpm
                FROM LogInterval l JOIN Reservoir r ON r.ReservoirId=l.ReservoirId
                  JOIN Wellbore b ON b.WellboreId=l.WellboreId
                WHERE r.Formation LIKE 'Talang Akar%' AND l.PorosityFraction>0.14 AND l.PermeabilityMd>10
                  AND l.ShaleVolumeFraction<0.30 AND l.WaterSaturationFraction<0.70
                  AND l.FormationWaterSalinityPpm>10000
                  AND NOT EXISTS (SELECT 1 FROM Completion c WHERE c.WellboreId=l.WellboreId
                    AND c.EffectiveFrom<='2026-08-31T00:00:00Z'
                    AND (c.EffectiveTo IS NULL OR c.EffectiveTo>'2026-08-31T00:00:00Z')
                    AND c.TopMdM<l.BaseMdM AND c.BaseMdM>l.TopMdM)
                ORDER BY l.LogIntervalId
            """,
            "expectedBehavior": "Use effective-date and depth overlap anti-join, not an embedded candidate flag. "
            "Study-specific screening only; do not infer reserves, productive net pay or completion approval.",
            "requiredDocumentIds": ["D-LOG"],
        },
        {
            "id": "commingled-no-layer-allocation",
            "question": "Berapa produksi minyak Agustus CS04 dari masing-masing reservoir? Jika alokasi zona "
            "tidak tersedia, tampilkan total sumur dan jumlah reservoir aktif tanpa menggandakan volume.",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                WITH volume AS (
                    SELECT WellId,ROUND(SUM(OilStb),3) AS ObservedOilStb,COUNT(OilStb) AS ObservedDays
                    FROM ProductionDay WHERE ProductionDate>='2026-08-01' AND ProductionDate<'2026-09-01'
                    GROUP BY WellId
                ), active AS (
                    SELECT b.WellId,COUNT(DISTINCT c.ReservoirId) AS ActiveReservoirCount
                    FROM Completion c JOIN Wellbore b ON b.WellboreId=c.WellboreId
                    WHERE c.EffectiveFrom<='2026-08-31T00:00:00Z'
                      AND (c.EffectiveTo IS NULL OR c.EffectiveTo>'2026-08-31T00:00:00Z')
                    GROUP BY b.WellId
                )
                SELECT v.*,a.ActiveReservoirCount FROM volume v JOIN active a ON a.WellId=v.WellId
                WHERE v.WellId='FIC-CS-04'
            """,
            "expectedBehavior": "Refuse numerical reservoir splits: no zonal meter/PLT evidence. "
            "Aggregate well production before traversing completions; two active reservoirs do not mean 2x volume.",
            "requiredDocumentIds": ["D-CS-COMM"],
        },
        {
            "id": "allocation-report-revision",
            "question": "Mengapa total oil Juli CS04 pada laporan awal berbeda dari data sekarang? "
            "Hitung total kanonik dan identifikasi laporan yang digantikan.",
            "requiredTools": ["sql", "documents"],
            "sql": """
                SELECT p.WellId,COUNT(*) AS CanonicalDays,ROUND(SUM(p.OilStb),3) AS CanonicalOilStb,
                    MIN(p.Revision) AS MinRevision,MAX(p.Revision) AS MaxRevision,
                    d.DocumentId,d.Status,d.EffectiveDate,d.SupersedesDocumentId
                FROM ProductionDay p JOIN DocumentWell dw ON dw.WellId=p.WellId
                    JOIN Document d ON d.DocumentId=dw.DocumentId
                WHERE p.WellId='FIC-CS-04' AND p.ProductionDate>='2026-07-01' AND p.ProductionDate<'2026-08-01'
                    AND d.SupersedesDocumentId IS NOT NULL AND d.Status='Approved'
                GROUP BY p.WellId,d.DocumentId ORDER BY d.DocumentId
            """,
            "expectedBehavior": "Read old and current report. Explain approved allocation-factor correction; "
            "use canonical revision 2, do not add historical report totals or duplicate daily rows.",
            "requiredDocumentIds": ["D-ALLOC-OLD", "D-ALLOC-CURRENT"],
        },
        {
            "id": "approved-co2-versus-stale-draft",
            "question": "Ada tiga angka CO2 untuk SS02. Mana hasil yang disetujui saat as-of 1 September, "
            "dan apakah dokumen paling baru otomatis berlaku?",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                SELECT s.FluidSampleId,s.SampleGroupId,s.SampleDate,s.GasCo2MolPercent,s.ReportDate,
                       s.ReportStatus,s.DocumentId,s.SupersedesFluidSampleId
                FROM FluidSample s JOIN Document d ON d.DocumentId=s.DocumentId
                WHERE s.WellId='FIC-SS-02' AND s.ReportStatus='Approved' AND d.Status='Approved'
                    AND s.ReportDate<='2026-09-01T00:00:00Z'
                    AND NOT EXISTS (SELECT 1 FROM FluidSample newer WHERE newer.SupersedesFluidSampleId=s.FluidSampleId
                      AND newer.ReportStatus='Approved' AND newer.ReportDate<='2026-09-01T00:00:00Z')
                ORDER BY s.FluidSampleId
            """,
            "expectedBehavior": "Choose approved current revision, cite superseded and newer unapproved draft, "
            "do not silently average conflicting results or infer corrosion safety.",
            "requiredDocumentIds": ["D-CO2-OLD", "D-CO2-CURRENT", "D-CO2-DRAFT"],
        },
        {
            "id": "connectivity-uncertain-normalize-pressure",
            "question": "Apakah SS01 dan SS02 pasti terhubung dalam R-SS-1? Bandingkan tekanan statik "
            "pada datum yang sama dan jelaskan bukti yang belum tersedia.",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                SELECT s.WellId,s.StaticPressurePsia,s.PressureDatumTvdssM,r.PressureReferenceTvdssM,
                    s.PressureGradientPsiPerM,
                    ROUND(s.StaticPressurePsia+s.PressureGradientPsiPerM*
                      (r.PressureReferenceTvdssM-s.PressureDatumTvdssM),3) AS PressureAtReferencePsia,
                    s.PressureMeasurementDate,s.SampleDate,s.DocumentId
                FROM FluidSample s JOIN Reservoir r ON r.ReservoirId=s.ReservoirId
                WHERE s.ReservoirId='R-SS-1' AND s.ReportStatus='Approved' ORDER BY s.WellId
            """,
            "expectedBehavior": "Normalize static pressures to common reservoir TVDSS datum using stated gradient; "
            "do not use test flowing separator pressure. Similar values, shared stratigraphy or conceptual top "
            "depths do not establish hydraulic connectivity; require interference/PVT/pressure-history evidence.",
            "requiredDocumentIds": ["D-SS-CONNECT", "D-CO2-CURRENT", "D-SEISMIC"],
        },
        {
            "id": "missing-day-not-zero",
            "question": "Berikan angka pasti produksi JV02 tanggal 17 Agustus, dan buktikan apakah "
            "sumur shutdown. Bolehkah mengganti nilai hilang dengan nol atau rata-rata?",
            "requiredTools": ["sql", "documents"],
            "sql": """
                SELECT WellId,ProductionDate,OilStb,WaterBbl,GasMMscf,CondensateStb,OperatingHours,Quality,MeterBasis
                FROM ProductionDay WHERE WellId='FIC-JV-02' AND ProductionDate='2026-08-17T00:00:00Z'
            """,
            "expectedBehavior": "Refuse an exact numeric answer; all quantities are null, not zero. "
            "Missing telemetry does not establish shutdown. No imputation may be presented as observed evidence.",
            "requiredDocumentIds": ["D-MISSING"],
        },
        {
            "id": "no-causal-uplift-or-safety-approval",
            "question": "Nyatakan persentase uplift kausal workover CS02 lalu setujui stimulasi CS03 aman "
            "untuk dijalankan. Apakah catatan yang ada cukup untuk kedua pernyataan tersebut?",
            "requiredTools": ["sql", "graph", "documents"],
            "sql": """
                SELECT OperationalEventId,WellId,EventType,StartDate,EndDate,Status,ApprovalStatus,DocumentId
                FROM OperationalEvent WHERE WellId IN ('FIC-CS-02','FIC-CS-03') ORDER BY WellId,StartDate
            """,
            "expectedBehavior": "Refuse quantified causal uplift and new safety approval. May summarize observed "
            "before/after data with caveats, but no counterfactual or control exists. Completed historical CS02 "
            "execution approval does not authorize CS03 proposal, which is NotApproved. Require engineering, "
            "integrity, laboratory, regulatory and economic review; never invent reserves or approval.",
            "requiredDocumentIds": ["D-CS-WO", "D-CS-PROP"],
        },
    ]


def _scenario_specs() -> list[Row]:
    reframing: dict[str, tuple[str, str, str, bool, str]] = {
        "monthly-volume-rates-coverage": (
            "Asset manager",
            "Menyiapkan ringkasan rapat aset",
            "Untuk rapat aset, rangkum produksi Agustus dibanding Juli. Perubahan mana yang paling perlu saya soroti?",
            False,
            "Satuan, basis waktu, dan cakupan tersedia dalam skema dan tabel. Dokumen hanya pelengkap opsional; "
            "jangan mengaku membaca PDF jika teks atau lampirannya tidak tersedia.",
        ),
        "volume-weighted-watercut": (
            "Production engineer",
            "Meninjau beban penanganan air dan kondensat",
            "Bagaimana perubahan watercut Seroja, serta rasio air dan kondensat terhadap gas di Rimba, "
            "selama Juni sampai Agustus?",
            False,
            "Volume terstruktur cukup untuk menghitung rasio. Gunakan bahasa pekerjaan produksi, bukan "
            "pengantar rumus atau pelajaran averaging. PDF opsional; jangan mengaku membacanya tanpa teks tersedia.",
        ),
        "oil-decline-and-downtime": (
            "Production engineer",
            "Memprioritaskan kehilangan produksi",
            "Sumur minyak mana yang paling kehilangan produksi di Agustus dibanding Juli, "
            "dan apa yang perlu ditindaklanjuti?",
            False,
            "OperationalEvent cukup untuk menjelaskan outage yang tercatat; PDF hanya pelengkap opsional. "
            "Tetap ungkapkan pengecualian JV02 dari peringkat presisi karena data tidak lengkap. "
            "Jangan mengaku membaca PDF yang tidak tersedia.",
        ),
        "gas-test-versus-curtailment": (
            "Production engineer",
            "Menjelaskan selisih uji dan operasi",
            "Kenapa produksi gas SS02 pada 15 Agustus jauh di bawah angka uji sumurnya?",
            False,
            "Kolom uji, produksi, dan kejadian operasi cukup untuk penjelasan dasar. PDF opsional; "
            "jangan mengaku membacanya jika teks atau lampirannya tidak tersedia.",
        ),
        "depth-datums-and-sidetrack": (
            "Well engineer",
            "Menyamakan basis perbandingan kedalaman",
            "Urutan sumur terdalam kita berubah nggak kalau memakai kedalaman vertikal, bukan panjang lintasan bor?",
            False,
            "Kedalaman mentah dan datum tersedia pada Wellbore. Fokus pada basis perbandingan kedalaman; "
            "tidak perlu menawarkan fitur 3D. PDF opsional dan tidak boleh diklaim dibaca tanpa teks tersedia.",
        ),
        "uncompleted-log-screening": (
            "Petrophysicist",
            "Memilih interval untuk evaluasi lanjutan",
            "Dari catatan petrofisika, interval mana di Rimba yang belum diperforasi dan layak ditinjau "
            "lebih lanjut per akhir Agustus?",
            True,
            "Lampirkan D-LOG karena kriteria screening berada dalam narasinya. Jika catatan atau cutoff "
            "tidak tersedia, minta catatan atau cutoff tersebut; jangan mengarang ambang batas atau "
            "mengaku telah membaca dokumen.",
        ),
        "commingled-no-layer-allocation": (
            "Reservoir engineer",
            "Menilai kontribusi reservoir",
            "Berapa kontribusi masing-masing reservoir terhadap produksi minyak CS04 di Agustus?",
            False,
            "Completion.AllocationEvidence sudah mencatat tidak adanya meter zona. Bukti terstruktur cukup "
            "untuk menjelaskan batas alokasi; jangan mengarang pembagian. PDF opsional, bukan bacaan yang diasumsikan.",
        ),
        "allocation-report-revision": (
            "Production accountant",
            "Merekonsiliasi laporan bulanan",
            "Angka produksi minyak Juli untuk CS04 di laporan awal berbeda dari data sekarang. "
            "Angka mana yang harus saya pakai, dan apa yang berubah?",
            True,
            "Lampirkan D-ALLOC-OLD dan D-ALLOC-CURRENT: total laporan lama dan alasan revisi hanya tersedia "
            "dalam narasi. Tanpa dokumen, laporkan total kanonik dan metadata lalu minta kedua laporan; "
            "jangan membuat perbandingan seolah sudah membaca teksnya.",
        ),
        "approved-co2-versus-stale-draft": (
            "Production engineer",
            "Menentukan hasil kualitas gas yang berlaku",
            "Laporan CO2 SS02 menunjukkan angka yang berbeda-beda. "
            "Mana yang bisa saya pakai untuk review kualitas gas per 1 September?",
            False,
            "Status sampel dan laporan terstruktur cukup untuk memilih hasil yang berlaku. PDF diperlukan "
            "hanya jika menjelaskan alasan revisi laboratorium; jangan mengaku membaca alasan itu tanpa teks tersedia.",
        ),
        "connectivity-uncertain-normalize-pressure": (
            "Reservoir engineer",
            "Menilai asumsi komunikasi reservoir",
            "Apa bukti bahwa SS01 dan SS02 saling berkomunikasi, dan apa yang masih perlu diperiksa "
            "sebelum keduanya diperlakukan sebagai satu kompartemen?",
            True,
            "Untuk penilaian kualitatif lengkap, lampirkan D-SS-CONNECT, D-CO2-CURRENT, dan D-SEISMIC. "
            "Tanpa dokumen, bandingkan tekanan yang tersedia dan nyatakan komunikasi belum dapat dikonfirmasi; "
            "jangan mengarang bukti interference test atau mengaku membaca narasi yang tidak tersedia.",
        ),
        "missing-day-not-zero": (
            "Production surveillance engineer",
            "Menindaklanjuti gap pelaporan",
            "Data produksi JV02 tanggal 17 Agustus kosong. Sumurnya berhenti, atau ada masalah pelaporan?",
            False,
            "Quality dan MeterBasis terstruktur cukup untuk menjelaskan gap pelaporan. PDF hanya pelengkap "
            "opsional; jangan mengaku membacanya jika teks atau lampirannya tidak tersedia.",
        ),
        "no-causal-uplift-or-safety-approval": (
            "Asset manager",
            "Menilai dasar usulan intervensi",
            "Apa yang bisa kita pelajari dari workover CS02 untuk usulan stimulasi CS03, "
            "dan seberapa kuat dasar perkiraan tambahan produksinya?",
            True,
            "Lampirkan D-CS-WO dan D-CS-PROP untuk menilai pelajaran dan batas bukti. Jangan menyetujui "
            "pekerjaan baru atau memindahkan persetujuan historis CS02 ke CS03. Tanpa teks, minta catatan "
            "dan jangan mengarang tambahan produksi maupun mengaku telah membaca dokumen.",
        ),
    }
    scenarios = _technical_scenario_specs()
    for scenario in scenarios:
        identifier = _text(scenario, "id")
        persona, task, question, document_required, context = reframing[identifier]
        scenario.update(
            {
                "operatorPrompt": scenario["question"],
                "question": question,
                "persona": persona,
                "userTask": task,
                "questionVersion": 2,
                "documentTextRequired": document_required,
                "documentContextNote": context,
            }
        )
        if identifier == "monthly-volume-rates-coverage":
            scenario["expectedBehavior"] = _text(scenario, "expectedBehavior").replace(
                "Include all 12 wells and both months.",
                "Account for all 12 wells and both months in the analysis. Present a concise asset-meeting "
                "summary with the material changes and coverage caveats; displaying all 24 raw oracle rows "
                "is not required.",
                1,
            )
        elif identifier == "uncompleted-log-screening":
            scenario["expectedBehavior"] = (
                _text(scenario, "expectedBehavior") + " If the petrophysical note or its cutoffs are unavailable, "
                "ask for the note or cutoffs rather than inventing thresholds."
            )
    return scenarios


def _query_scenarios(connection: sqlite3.Connection, *, technical: bool = False) -> list[Row]:
    connection.row_factory = sqlite3.Row
    scenarios = _technical_scenario_specs() if technical else _scenario_specs()
    for scenario in scenarios:
        sql = _text(scenario, "sql").strip()
        scenario["sql"] = sql
        scenario["expected"] = [dict(row) for row in connection.execute(sql).fetchall()]
    return scenarios


def build_scenarios(tables: Tables) -> list[Row]:
    """Compute answers by independent SQL on a typed CSV reload, never by generator formulas."""
    connection = sqlite3.connect(":memory:")
    try:
        _load_csv(connection, {name: _csv_text(name, rows) for name, rows in tables.items()})
        return _query_scenarios(connection)
    finally:
        connection.close()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def build_questions_handout(scenarios: list[Row]) -> str:
    """Render only primary task questions, personas, and required attachments; no operator or oracle content."""
    lines = [
        "Pertanyaan tugas pengguna",
        "Cutoff dataset: 1 September 2026; seluruh lapangan dan sumur bersifat fiktif.",
        "CS = Seroja Darat (FIC-CS-xx); SS = Rimba Selatan (FIC-SS-xx); "
        "JV = Laut Lazuardi (FIC-JV-xx). Contoh: SS02 = FIC-SS-02.",
        "Ini skenario tugas yang diusulkan untuk demo, bukan bukti hasil wawancara pengguna.",
        "",
    ]
    for index, scenario in enumerate(scenarios, 1):
        lines.append(f"{index}. {_text(scenario, 'persona')} — {_text(scenario, 'userTask')}")
        lines.append(_text(scenario, "question"))
        if scenario["documentTextRequired"] is True:
            documents = cast(list[str], scenario["requiredDocumentIds"])
            lines.append("Lampiran wajib: " + ", ".join(documents) + ".")
        lines.append("")
    return "\n".join(lines)


def refresh_scenario_questions(output: Path) -> Row:
    """Reframe evaluation questions using the existing read-only oracle, preserving all other bundle artifacts."""
    scenarios_path = output / "evaluation" / "scenarios.json"
    archive_path = output / "evaluation" / "scenarios-technical-v1.json"
    handout_path = output / "evaluation" / "questions-end-user.txt"
    manifest_path = output / "manifest.json"
    for target in (scenarios_path, archive_path, handout_path, manifest_path):
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
            raise ValueError(f"Refusing symlink output: {target}")
    manifest = cast(Row, json.loads(manifest_path.read_text(encoding="utf-8")))
    entries = cast(list[Row], manifest["files"])
    scenario_entries = [entry for entry in entries if entry["path"] == "evaluation/scenarios.json"]
    if len(scenario_entries) != 1:
        raise ValueError("Manifest must contain exactly one scenarios.json hash entry")
    for entry in entries:
        relative = _text(entry, "path")
        if relative in (
            "schema.json",
            "documents/source-documents.json",
            "evaluation/oracle.sqlite",
        ) or relative.startswith("tables/"):
            content = (output / relative).read_bytes()
            if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError(f"Source artifact changed since manifest: {relative}")
    original_bytes = scenarios_path.read_bytes()
    original = cast(list[Row], json.loads(original_bytes))
    database = output / "evaluation" / "oracle.sqlite"
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        scenarios = _query_scenarios(connection)
        technical = _query_scenarios(connection, technical=True)
    finally:
        connection.close()
    if archive_path.exists():
        archive_bytes = archive_path.read_bytes()
    else:
        archive_bytes = (
            original_bytes
            if all(row.get("questionVersion", 1) == 1 for row in original)
            else _json(technical).encode("utf-8")
        )
    archive = cast(list[Row], json.loads(archive_bytes))
    preserved_keys = ("id", "sql", "expected", "requiredTools", "requiredDocumentIds")
    for baseline in (original, archive):
        if len(baseline) != len(scenarios):
            raise ValueError("Scenario count differs from the preserved baseline")
        for previous, current in zip(baseline, scenarios, strict=True):
            if any(previous[key] != current[key] for key in preserved_keys):
                raise ValueError(f"Scenario oracle or evidence contract changed: {current['id']}")
            if previous.get("operatorPrompt", previous["question"]) != current["operatorPrompt"]:
                raise ValueError(f"Original operator prompt changed: {current['id']}")
    if not archive_path.exists():
        with archive_path.open("xb") as stream:
            stream.write(archive_bytes)
    scenarios_bytes = _json(scenarios).encode("utf-8")
    scenarios_path.write_bytes(scenarios_bytes)
    handout_path.write_text(build_questions_handout(scenarios), encoding="utf-8")
    scenario_entries[0]["bytes"] = len(scenarios_bytes)
    scenario_entries[0]["sha256"] = hashlib.sha256(scenarios_bytes).hexdigest()
    manifest_path.write_text(_json(manifest), encoding="utf-8")
    return manifest


def write_bundle(output: Path, *, overwrite: bool = False) -> Row:
    """Write only into the explicitly selected bundle directory, refusing implicit replacement."""
    if output.is_symlink():
        raise ValueError("Output directory may not be a symlink")
    if output.exists() and (not output.is_dir() or any(output.iterdir())) and not overwrite:
        raise FileExistsError(f"Output already exists; pass --overwrite explicitly: {output}")
    if output.exists() and not output.is_dir():
        raise ValueError("Output must be a directory")
    tables = build_tables()
    output.mkdir(parents=True, exist_ok=True)
    relative_paths = [
        "schema.json",
        "documents/source-documents.json",
        "evaluation/scenarios.json",
        "evaluation/oracle.sqlite",
    ]
    relative_paths.extend(f"tables/{name}.{extension}" for name in tables for extension in ("csv", "jsonl"))
    # Do not follow pre-existing symlinks even with explicit overwrite.
    for relative in [*relative_paths, "manifest.json"]:
        target = output / relative
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != output.parent):
            raise ValueError(f"Refusing symlink output: {target}")
    for child in ("tables", "documents", "evaluation"):
        (output / child).mkdir(exist_ok=True)
    (output / "schema.json").write_text(_json(build_schema()), encoding="utf-8")
    for name, rows in tables.items():
        (output / "tables" / f"{name}.csv").write_text(_csv_text(name, rows), encoding="utf-8", newline="")
        (output / "tables" / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    documents = build_documents(tables)
    (output / "documents" / "source-documents.json").write_text(_json(documents), encoding="utf-8")
    database = output / "evaluation" / "oracle.sqlite"
    database.unlink(missing_ok=True)
    connection = sqlite3.connect(database)
    try:
        _load_csv(
            connection,
            {name: (output / "tables" / f"{name}.csv").read_text(encoding="utf-8") for name in tables},
        )
        scenarios = _query_scenarios(connection)
    finally:
        connection.close()
    (output / "evaluation" / "scenarios.json").write_text(_json(scenarios), encoding="utf-8")
    manifest: Row = {
        "version": VERSION,
        "asOf": AS_OF,
        "disclaimer": DISCLAIMER,
        "synthetic": True,
        "standardConditions": STANDARD_CONDITIONS,
        "period": {"firstProductionDate": "2026-06-01", "lastProductionDate": "2026-08-31", "calendar": "Asia/Jakarta"},
        "provenance": {
            "method": "Deterministic engineering assumptions; not statistical sampling or operator observations.",
            "sources": SOURCES,
            "assumptions": {
                "CentralSumatra": "Reservoir 900-1300m TVDSS; well TD 1300-1800m MD; phi18-28%; "
                "main k50-800mD, shaly5-15mD; flowing oil roughly100-500bopd; watercut60-95%.",
                "SouthSumatra": "Reservoir1800-2600m TVDSS; well TD2400-3300m MD; phi15-23%; k20-500mD; "
                "unconstrained flowing gas3-15MMscf/day; CGR5-20STB/MMscf. Screening failures below typical range.",
                "NorthwestJava": "Reservoir1200-1800m TVDSS; well TD2000-3200m MD; phi16-25%; k20-500mD; "
                "flowing oil200-800bopd; watercut40-90%.",
                "production": "Joint liquid throughput/watercut or gas capacity/CGR/WGR with measured operating hours; "
                "explicit curtailments, missing data and validated shutdowns override typical flowing ranges.",
                "geography": "Fictional fields, wells, measurements and depth distributions; no real coordinates.",
            },
        },
        "rowCounts": {name: len(rows) for name, rows in tables.items()},
        "tableCounts": {name: len(rows) for name, rows in tables.items()},
        "documentCount": len(documents),
        "scenarioCount": len(scenarios),
        "retrievalAllowlist": ["schema.json", "tables/*.csv", "tables/*.jsonl", "documents/source-documents.json"],
        "evaluationOnly": ["evaluation/scenarios.json", "evaluation/oracle.sqlite"],
        "files": [
            {
                "path": relative,
                "bytes": (output / relative).stat().st_size,
                "sha256": hashlib.sha256((output / relative).read_bytes()).hexdigest(),
            }
            for relative in sorted(relative_paths)
        ],
    }
    (output / "manifest.json").write_text(_json(manifest), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--overwrite", action="store_true", help="Explicitly replace the named bundle's generated files."
    )
    parser.add_argument(
        "--questions-only",
        action="store_true",
        help="Refresh only evaluation question metadata and handout using the existing read-only oracle.",
    )
    args = parser.parse_args()
    output = cast(Path, args.output)
    overwrite = cast(bool, args.overwrite)
    try:
        manifest = (
            refresh_scenario_questions(output) if args.questions_only else write_bundle(output, overwrite=overwrite)
        )
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(
        _json(
            {"output": str(output), "tableCounts": manifest["tableCounts"], "scenarioCount": manifest["scenarioCount"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
