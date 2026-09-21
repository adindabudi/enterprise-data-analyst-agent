from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import json
import math
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "energy_demo_data.py"
V1_ORACLE_CONTRACT_SHA256 = "0fb0f5d97f24594420f700f0cb068768338e2163f3f1adeed120ad7e11d70ca7"
PRESERVED_SCENARIO_KEYS = ("id", "sql", "expected", "requiredTools", "requiredDocumentIds")
REVIEWED_TASKS = {
    "monthly-volume-rates-coverage": (
        "Asset manager",
        "Menyiapkan ringkasan rapat aset",
        "Untuk rapat aset, rangkum produksi Agustus dibanding Juli. Perubahan mana yang paling perlu saya soroti?",
    ),
    "volume-weighted-watercut": (
        "Production engineer",
        "Meninjau beban penanganan air dan kondensat",
        "Bagaimana perubahan watercut Seroja, serta rasio air dan kondensat terhadap gas di Rimba, "
        "selama Juni sampai Agustus?",
    ),
    "oil-decline-and-downtime": (
        "Production engineer",
        "Memprioritaskan kehilangan produksi",
        "Sumur minyak mana yang paling kehilangan produksi di Agustus dibanding Juli, "
        "dan apa yang perlu ditindaklanjuti?",
    ),
    "gas-test-versus-curtailment": (
        "Production engineer",
        "Menjelaskan selisih uji dan operasi",
        "Kenapa produksi gas SS02 pada 15 Agustus jauh di bawah angka uji sumurnya?",
    ),
    "depth-datums-and-sidetrack": (
        "Well engineer",
        "Menyamakan basis perbandingan kedalaman",
        "Urutan sumur terdalam kita berubah nggak kalau memakai kedalaman vertikal, bukan panjang lintasan bor?",
    ),
    "uncompleted-log-screening": (
        "Petrophysicist",
        "Memilih interval untuk evaluasi lanjutan",
        "Dari catatan petrofisika, interval mana di Rimba yang belum diperforasi dan layak ditinjau "
        "lebih lanjut per akhir Agustus?",
    ),
    "commingled-no-layer-allocation": (
        "Reservoir engineer",
        "Menilai kontribusi reservoir",
        "Berapa kontribusi masing-masing reservoir terhadap produksi minyak CS04 di Agustus?",
    ),
    "allocation-report-revision": (
        "Production accountant",
        "Merekonsiliasi laporan bulanan",
        "Angka produksi minyak Juli untuk CS04 di laporan awal berbeda dari data sekarang. "
        "Angka mana yang harus saya pakai, dan apa yang berubah?",
    ),
    "approved-co2-versus-stale-draft": (
        "Production engineer",
        "Menentukan hasil kualitas gas yang berlaku",
        "Laporan CO2 SS02 menunjukkan angka yang berbeda-beda. "
        "Mana yang bisa saya pakai untuk review kualitas gas per 1 September?",
    ),
    "connectivity-uncertain-normalize-pressure": (
        "Reservoir engineer",
        "Menilai asumsi komunikasi reservoir",
        "Apa bukti bahwa SS01 dan SS02 saling berkomunikasi, dan apa yang masih perlu diperiksa "
        "sebelum keduanya diperlakukan sebagai satu kompartemen?",
    ),
    "missing-day-not-zero": (
        "Production surveillance engineer",
        "Menindaklanjuti gap pelaporan",
        "Data produksi JV02 tanggal 17 Agustus kosong. Sumurnya berhenti, atau ada masalah pelaporan?",
    ),
    "no-causal-uplift-or-safety-approval": (
        "Asset manager",
        "Menilai dasar usulan intervensi",
        "Apa yang bisa kita pelajari dari workover CS02 untuk usulan stimulasi CS03, "
        "dan seberapa kuat dasar perkiraan tambahan produksinya?",
    ),
}


@pytest.fixture(scope="module")
def demo() -> ModuleType:
    specification = importlib.util.spec_from_file_location("energy_demo_data", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tables(demo):
    return demo.build_tables()


@pytest.fixture(scope="module")
def scenarios(demo, tables):
    return {scenario["id"]: scenario for scenario in demo.build_scenarios(tables)}


@pytest.fixture
def output_root():
    # Keep test artifacts in the explicitly owned output subtree, not system temporary directories.
    directory = ROOT / ".artifacts" / "energy-demo" / ".test-runs" / uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
        if not any(directory.parent.iterdir()):
            directory.parent.rmdir()


def test_deterministic_builds_and_no_shared_mutable_rows(demo, tables):
    assert demo.build_tables() == tables
    assert demo.build_schema() == demo.build_schema()
    assert demo.build_documents(tables) == demo.build_documents(tables)
    assert demo.build_scenarios(tables) == demo.build_scenarios(tables)
    second = demo.build_tables()
    second["Well"][0]["Name"] = "changed"
    assert tables["Well"][0]["Name"] != "changed"
    assert len({id(row) for rows in tables.values() for row in rows}) == sum(map(len, tables.values()))


def test_schema_is_explicit_and_entities_have_no_answer_fields(demo, tables):
    schema = demo.build_schema()
    assert schema["version"] == demo.VERSION
    assert schema["asOf"] == "2026-09-01T00:00:00Z"
    assert "synthetic" in schema["disclaimer"].lower()
    assert set(tables) == {table["name"] for table in schema["tables"]}
    for spec in schema["tables"]:
        assert re.fullmatch(r"[A-Z][a-zA-Z0-9]*", spec["name"])
        assert spec["description"].startswith("One ")
        assert isinstance(spec["synonyms"], list) and spec["synonyms"]
        assert spec["ontology"] is (spec["name"] != "DocumentWell")
        columns = {column["name"]: column for column in spec["columns"]}
        assert columns[spec["key"]]["type"] == "String"
        assert columns[spec["key"]]["nullable"] is False
        for column in columns.values():
            assert re.fullmatch(r"[A-Z][a-zA-Z0-9]*", column["name"])
            assert column["type"] in {"String", "BigInt", "Double", "Boolean", "DateTime"}
            assert isinstance(column["nullable"], bool) and column["description"]
        for row in tables[spec["name"]]:
            assert set(row) == set(columns)
            if spec["ontology"]:
                assert row["Synthetic"] is True
    assert set(tables["Well"][0]) == {
        "WellId",
        "FieldId",
        "Name",
        "ProductionSystem",
        "FirstProductionDate",
        "Synthetic",
    }
    serialized = json.dumps(tables).lower()
    for forbidden in ("expectedbehavior", "requiredtools", "requiredocumentids", "answerkey", "candidaterank"):
        assert forbidden not in serialized


def test_counts_all_keys_and_graph_foreign_keys(demo, tables):
    assert {name: len(tables[name]) for name in ("Field", "Reservoir", "Well", "Wellbore", "ProductionDay")} == {
        "Field": 3,
        "Reservoir": 6,
        "Well": 12,
        "Wellbore": 14,
        "ProductionDay": 1104,
    }
    schema = demo.build_schema()
    keys = {}
    for spec in schema["tables"]:
        values = [row[spec["key"]] for row in tables[spec["name"]]]
        assert len(values) == len(set(values))
        assert all(isinstance(value, str) and value for value in values)
        keys[spec["name"]] = set(values)
    relations = schema["relationships"]
    assert len({edge["name"] for edge in relations}) == len(relations)
    for edge in relations:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", edge["name"])
        assert edge["cardinality"] in {"ManyToOne", "ManyToMany"}
        for row in tables[edge["table"]]:
            assert row[edge["source_key"]] in keys[edge["source"]]
            target = row[edge["target_key"]]
            assert target is None or target in keys[edge["target"]]
    assert [(edge["target"], edge["target_key"]) for edge in relations if edge["source"] == "ProductionDay"] == [
        ("Well", "WellId")
    ]
    assert len({(row["DocumentId"], row["WellId"]) for row in tables["DocumentWell"]}) == len(tables["DocumentWell"])


def test_production_calendar_coverage_and_recorded_asof(demo, tables):
    assert Counter(row["WellId"] for row in tables["ProductionDay"]) == {well["WellId"]: 92 for well in tables["Well"]}
    assert Counter(row["ProductionDate"][:7] for row in tables["ProductionDay"]) == {
        "2026-06": 360,
        "2026-07": 372,
        "2026-08": 372,
    }
    pairs = {(row["WellId"], row["ProductionDate"]) for row in tables["ProductionDay"]}
    assert len(pairs) == 1104
    for row in tables["ProductionDay"]:
        start, end = (datetime.fromisoformat(row[key]) for key in ("PeriodStartUtc", "PeriodEndUtc"))
        label = datetime.fromisoformat(row["ProductionDate"])
        assert end - start == timedelta(hours=24)
        assert start + timedelta(hours=7) == label
        assert row["ProductionDate"].endswith("T00:00:00Z")
        assert end <= datetime.fromisoformat(row["RecordedAt"]) <= datetime.fromisoformat(demo.AS_OF)
    for spec in demo.build_schema()["tables"]:
        for column in spec["columns"]:
            if column["type"] == "DateTime":
                assert all(
                    row[column["name"]] is None or row[column["name"]].endswith("Z") for row in tables[spec["name"]]
                )


def test_missing_and_shutdown_are_different_and_gas_units_coherent(tables):
    quantities = ("OilStb", "WaterBbl", "GasMMscf", "CondensateStb", "OperatingHours")
    missing = [row for row in tables["ProductionDay"] if row["Quality"] == "Missing"]
    assert len(missing) == 1
    assert missing[0]["WellId"] == "FIC-JV-02"
    assert all(missing[0][key] is None for key in quantities)
    shutdowns = [row for row in tables["ProductionDay"] if row["Quality"] == "ShutdownValidated"]
    assert len(shutdowns) == 19
    assert all(row[key] == 0 for row in shutdowns for key in quantities)
    for row in tables["ProductionDay"]:
        if row["Quality"] == "Missing":
            continue
        assert all(row[key] >= 0 and math.isfinite(row[key]) for key in quantities)
        assert 0 <= row["OperatingHours"] <= 24
        if row["WellId"].startswith("FIC-SS"):
            assert row["OilStb"] == 0
            assert 0 < row["GasMMscf"] < 15
            assert 5 <= row["CondensateStb"] / row["GasMMscf"] <= 20
        else:
            assert row["CondensateStb"] == 0
            if row["OilStb"] + row["WaterBbl"] > 0:
                assert 0.4 <= row["WaterBbl"] / (row["OilStb"] + row["WaterBbl"]) <= 0.95
                assert row["GasMMscf"] / row["OilStb"] == pytest.approx(
                    0.00075 if row["WellId"].startswith("FIC-CS") else 0.0012, abs=1e-8
                )


def test_physical_depths_petrophysics_and_half_open_completions(tables):
    bores = {row["WellboreId"]: row for row in tables["Wellbore"]}
    reservoirs = {row["ReservoirId"]: row for row in tables["Reservoir"]}
    wells = {row["WellId"]: row for row in tables["Well"]}
    for bore in bores.values():
        assert bore["TotalDepthMdM"] >= bore["TotalDepthTvdM"]
        assert bore["TotalDepthTvdssM"] == bore["TotalDepthTvdM"] - bore["ReferenceElevationM"]
        if bore["ParentWellboreId"]:
            assert bores[bore["ParentWellboreId"]]["WellId"] == bore["WellId"]
    for name in ("Completion", "LogInterval"):
        for row in tables[name]:
            bore = bores[row["WellboreId"]]
            assert 0 <= row["TopMdM"] < row["BaseMdM"] <= bore["TotalDepthMdM"]
            assert wells[bore["WellId"]]["FieldId"] == reservoirs[row["ReservoirId"]]["FieldId"]
            if name == "LogInterval":
                assert row["TopTvdssM"] < row["BaseTvdssM"] <= bore["TotalDepthTvdssM"]
                assert row["TopTvdssM"] + bore["ReferenceElevationM"] <= row["TopMdM"]
                assert row["BaseTvdssM"] + bore["ReferenceElevationM"] <= row["BaseMdM"]
                assert 0 <= row["PorosityFraction"] <= 1
                assert 0 <= row["WaterSaturationFraction"] <= 1
                assert 0 <= row["ShaleVolumeFraction"] <= 1
                assert row["PermeabilityMd"] > 0
            elif row["EffectiveTo"] is not None:
                assert row["EffectiveFrom"] < row["EffectiveTo"]
    for observation in tables["ProductionDay"]:
        active = [
            completion
            for completion in tables["Completion"]
            if bores[completion["WellboreId"]]["WellId"] == observation["WellId"]
            and completion["EffectiveFrom"] <= observation["ProductionDate"]
            and (completion["EffectiveTo"] is None or completion["EffectiveTo"] > observation["ProductionDate"])
        ]
        assert len(active) == (2 if observation["WellId"] == "FIC-CS-04" else 1)


def test_seismic_quantiles_are_versioned_not_3d_evidence(tables):
    assert len(tables["SeismicInterpretation"]) == 12
    for reservoir in tables["Reservoir"]:
        versions = [row for row in tables["SeismicInterpretation"] if row["ReservoirId"] == reservoir["ReservoirId"]]
        assert [row["Version"] for row in versions] == [1, 2]
        assert [row["Status"] for row in versions] == ["Superseded", "Current"]
        for row in versions:
            assert row["TopDepthQ10TvdssM"] <= row["TopDepthQ50TvdssM"] <= row["TopDepthQ90TvdssM"]
            assert "no acquired seismic" in row["EvidenceKind"]
            assert "P(depth<=q)=p" in row["UncertaintyExplanation"]
            assert "covariance" in row["UncertaintyExplanation"]


def test_consecutive_vertical_displacement_never_exceeds_along_hole_distance(tables):
    for bore in tables["Wellbore"]:
        points = [
            (0.0, -bore["ReferenceElevationM"]),
            (bore["TotalDepthMdM"], bore["TotalDepthTvdssM"]),
        ]
        for interval in tables["LogInterval"]:
            if interval["WellboreId"] == bore["WellboreId"]:
                points += [
                    (interval["TopMdM"], interval["TopTvdssM"]),
                    (interval["BaseMdM"], interval["BaseTvdssM"]),
                ]
        points.sort()
        for previous, current in pairwise(points):
            md_distance = current[0] - previous[0]
            vertical_distance = abs(current[1] - previous[1])
            assert vertical_distance <= md_distance + 1e-9, (bore["WellboreId"], previous, current)


def test_validator_rejects_locally_valid_but_globally_impossible_log_coordinates(demo, tables):
    changed = copy.deepcopy(tables)
    interval = next(row for row in changed["LogInterval"] if row["LogIntervalId"] == "L-CS-03-UNPERF")
    interval["TopMdM"] = 1270.0
    interval["BaseMdM"] = 1282.0
    assert interval["BaseTvdssM"] - interval["TopTvdssM"] <= interval["BaseMdM"] - interval["TopMdM"]
    with pytest.raises(ValueError, match="consecutive vertical depth change"):
        demo.validate_tables(changed)


def test_documents_metadata_many_to_many_and_observations_match(demo, tables, scenarios):
    documents = {document["documentId"]: document for document in demo.build_documents(tables)}
    assert len(documents) == len(tables["Document"]) == 15
    for metadata in tables["Document"]:
        document = documents[metadata["DocumentId"]]
        assert document["fileName"] == metadata["FileName"]
        assert document["title"] == metadata["Title"]
        assert document["status"] == metadata["Status"]
        assert document["effectiveDate"] == metadata["EffectiveDate"]
        assert document["supersedesDocumentId"] == metadata["SupersedesDocumentId"]
        assert document["wellIds"] == [
            link["WellId"] for link in tables["DocumentWell"] if link["DocumentId"] == document["documentId"]
        ]
        assert document["sections"] and document["wellIds"]
        assert all(section["heading"] and section["text"] for section in document["sections"])
    assert len(documents["D-BASE"]["wellIds"]) == 12
    for scenario in scenarios.values():
        assert all(identifier in documents for identifier in scenario["requiredDocumentIds"])
        for document in documents.values():
            assert scenario["question"] not in json.dumps(document, ensure_ascii=False)
    for sample in tables["FluidSample"]:
        if sample["WellId"] == "FIC-SS-02":
            text = "\n".join(section["text"] for section in documents[sample["DocumentId"]]["sections"])
            assert f"CO2={sample['GasCo2MolPercent']} mol%" in text
            assert sample["ReportStatus"] in text
    before_after = "\n".join(section["text"] for section in documents["D-CS-WO"]["sections"])
    for row in tables["ProductionDay"]:
        day = row["ProductionDate"][:10]
        if row["WellId"] == "FIC-CS-02" and ("2026-06-20" <= day < "2026-06-27" or "2026-07-02" <= day < "2026-07-09"):
            assert f"{day} WIB: oil={row['OilStb']} STB" in before_after


def test_monthly_oracle_uses_every_well_and_correct_denominators(tables, scenarios):
    results = scenarios["monthly-volume-rates-coverage"]["expected"]
    assert len(results) == 24
    assert {row["WellId"] for row in results} == {row["WellId"] for row in tables["Well"]}
    for result in results:
        observations = [
            row
            for row in tables["ProductionDay"]
            if row["WellId"] == result["WellId"] and row["ProductionDate"].startswith(result["Month"])
        ]
        valid = [row for row in observations if row["Quality"] != "Missing"]
        assert result["CalendarDays"] == 31
        assert result["ObservedDays"] == len(valid)
        assert result["MissingDays"] == 31 - len(valid)
        total_oil = sum(row["OilStb"] for row in valid)
        hours = sum(row["OperatingHours"] for row in valid)
        assert result["ObservedOilStb"] == pytest.approx(total_oil, abs=0.000001)
        assert result["ObservedOilPerCalendarDay"] == pytest.approx(total_oil / 31, abs=0.000001)
        assert result["OilPerOnstreamDay"] == pytest.approx(24 * total_oil / hours, abs=0.000001)
    incomplete = [row for row in results if row["MissingDays"]]
    assert [(row["WellId"], row["Month"], row["ObservedDays"]) for row in incomplete] == [("FIC-JV-02", "2026-08", 30)]


def test_weighted_watercut_differs_from_naive_and_no_gas_watercut(tables, scenarios):
    results = scenarios["volume-weighted-watercut"]["expected"]
    assert len(results) == 6
    oil_results = [row for row in results if row["Field"] == "Seroja Darat"]
    assert all(row["WeightedWatercut"] != row["NaiveMeanWatercut"] for row in oil_results)
    assert oil_results[0]["WeightedWatercut"] < oil_results[-1]["WeightedWatercut"]
    for result in oil_results:
        observations = [
            row
            for row in tables["ProductionDay"]
            if row["WellId"].startswith("FIC-CS") and row["ProductionDate"].startswith(result["Month"])
        ]
        water = sum(row["WaterBbl"] for row in observations)
        liquid = sum(row["WaterBbl"] + row["OilStb"] for row in observations)
        assert result["WeightedWatercut"] == pytest.approx(water / liquid, abs=1e-6)
    for result in results:
        if result["Field"] == "Rimba Selatan":
            assert result["WeightedWatercut"] is None
            assert result["NaiveMeanWatercut"] is None
            assert result["WgrBblPerMMscf"] > 0
            assert 5 <= result["CgrStbPerMMscf"] <= 20


def test_test_rate_is_not_daily_volume_and_restriction_is_observed(scenarios, tables):
    row = scenarios["gas-test-versus-curtailment"]["expected"][0]
    assert row["DurationHours"] == 6
    assert row["OperatingHours"] == 12
    assert row["GasRateMMscfPerDay"] != row["GasMMscf"]
    assert row["GasRateMMscfPerDay"] > row["ProductionOnstreamMMscfPerDay"]
    assert row["EventType"] == "CompressorCurtailment"
    assert "separator" in row["PressureLocation"]
    assert any(not row["Stabilized"] for row in tables["WellTest"])
    assert all(0 < row["DurationHours"] < 24 and 0 < row["Choke64thsIn"] <= 64 for row in tables["WellTest"])
    pressure_date = datetime.fromisoformat(tables["FluidSample"][0]["PressureMeasurementDate"])
    assert pressure_date < datetime.fromisoformat(tables["ProductionDay"][0]["PeriodStartUtc"])


def test_flowing_test_intervals_do_not_overlap_zero_hour_wib_days(tables):
    well_for_bore = {row["WellboreId"]: row["WellId"] for row in tables["Wellbore"]}
    rate_keys = ("OilRateStbPerDay", "WaterRateBblPerDay", "GasRateMMscfPerDay", "CondensateRateStbPerDay")
    for test in tables["WellTest"]:
        if not any(test[key] > 0 for key in rate_keys):
            continue
        test_start = datetime.fromisoformat(test["TestDate"])
        test_end = test_start + timedelta(hours=test["DurationHours"])
        well_id = well_for_bore[test["WellboreId"]]
        for day in tables["ProductionDay"]:
            if day["WellId"] != well_id or day["OperatingHours"] != 0:
                continue
            day_start = datetime.fromisoformat(day["PeriodStartUtc"])
            day_end = datetime.fromisoformat(day["PeriodEndUtc"])
            assert test_end <= day_start or test_start >= day_end, (test["WellTestId"], day["ProductionDate"])


@pytest.mark.parametrize(
    ("test_start", "should_overlap"),
    [
        ("2026-08-08T02:00:00Z", True),
        ("2026-08-04T16:30:00Z", True),
        ("2026-08-04T13:00:00Z", False),
        ("2026-08-14T17:00:00Z", False),
    ],
)
def test_validator_checks_test_duration_against_physical_wib_boundaries(demo, tables, test_start, should_overlap):
    changed = copy.deepcopy(tables)
    test = next(row for row in changed["WellTest"] if row["WellTestId"] == "T-FIC-JV-01-01")
    test["TestDate"] = test_start
    assert test["DurationHours"] == 4
    if should_overlap:
        with pytest.raises(ValueError, match="flowing test overlaps a zero-hour"):
            demo.validate_tables(changed)
    else:
        demo.validate_tables(changed)


def test_source_test_values_use_human_precision_and_explicit_observation_dates(demo, tables):
    documents = {document["documentId"]: document for document in demo.build_documents(tables)}
    gas_text = "\n".join(section["text"] for section in documents["D-GAS-TEST"]["sections"])
    oil_text = "\n".join(section["text"] for section in documents["D-LOG"]["sections"])
    assert "96.040 STB condensate/day" in gas_text
    assert "96.04000000000002" not in gas_text
    for test in tables["WellTest"]:
        text = gas_text if test["DocumentId"] == "D-GAS-TEST" else oil_text
        assert f"{test['WellTestId']}: start={test['TestDate']}" in text
        if test["DocumentId"] == "D-GAS-TEST":
            assert f"{test['CondensateRateStbPerDay']:.3f} STB condensate/day" in text


def test_depth_screening_and_commingled_oracles(scenarios, tables):
    deepest = scenarios["depth-datums-and-sidetrack"]["expected"]
    assert {row["WellboreId"] for row in deepest} == {"FIC-JV-03-ST1", "FIC-SS-04-M"}
    assert max(deepest, key=lambda row: row["TotalDepthMdM"])["BoreType"] == "Sidetrack"
    screened = scenarios["uncompleted-log-screening"]["expected"]
    assert [row["LogIntervalId"] for row in screened] == ["L-SS-03-UNPERF"]
    commingled = scenarios["commingled-no-layer-allocation"]["expected"][0]
    assert commingled["ActiveReservoirCount"] == 2
    assert commingled["ObservedDays"] == 31
    actual = sum(
        row["OilStb"]
        for row in tables["ProductionDay"]
        if row["WellId"] == "FIC-CS-04" and row["ProductionDate"].startswith("2026-08")
    )
    assert commingled["ObservedOilStb"] == pytest.approx(actual)


def test_current_report_revisions_and_cautious_decline(scenarios, tables, demo):
    decline = scenarios["oil-decline-and-downtime"]["expected"]
    assert len(decline) == 1
    assert decline[0]["WellId"] == "FIC-JV-01"
    assert decline[0]["EventType"] == "Shutdown"
    assert decline[0]["OilChangeStb"] < 0
    allocation = scenarios["allocation-report-revision"]["expected"][0]
    assert allocation["CanonicalDays"] == 31
    assert allocation["MinRevision"] == allocation["MaxRevision"] == 2
    assert allocation["SupersedesDocumentId"] == "D-ALLOC-OLD"
    documents = {row["documentId"]: row for row in demo.build_documents(tables)}
    assert f"{allocation['CanonicalOilStb']:.3f} STB" in documents["D-ALLOC-CURRENT"]["sections"][0]["text"]
    assert f"{allocation['CanonicalOilStb'] + 750:.3f} STB" in documents["D-ALLOC-OLD"]["sections"][0]["text"]
    current_co2 = scenarios["approved-co2-versus-stale-draft"]["expected"]
    assert len(current_co2) == 1
    assert current_co2[0]["GasCo2MolPercent"] == 4.1
    assert current_co2[0]["DocumentId"] == "D-CO2-CURRENT"
    draft = next(row for row in tables["FluidSample"] if row["ReportStatus"] == "Draft")
    assert draft["ReportDate"] > current_co2[0]["ReportDate"]
    assert draft["SupersedesFluidSampleId"] is None


def test_pressure_normalization_refusals_and_approvals(scenarios):
    pressure = scenarios["connectivity-uncertain-normalize-pressure"]["expected"]
    assert len(pressure) == 2
    assert len({row["PressureDatumTvdssM"] for row in pressure}) == 2
    assert len({row["PressureReferenceTvdssM"] for row in pressure}) == 1
    for row in pressure:
        assert row["PressureAtReferencePsia"] == pytest.approx(
            row["StaticPressurePsia"]
            + row["PressureGradientPsiPerM"] * (row["PressureReferenceTvdssM"] - row["PressureDatumTvdssM"])
        )
    missing = scenarios["missing-day-not-zero"]["expected"][0]
    assert missing["OilStb"] is None and missing["OperatingHours"] is None and missing["Quality"] == "Missing"
    work = scenarios["no-causal-uplift-or-safety-approval"]["expected"]
    assert [(row["Status"], row["ApprovalStatus"]) for row in work] == [
        ("Completed", "ApprovedExecution"),
        ("Planned", "NotApproved"),
    ]
    assert "Refuse quantified causal uplift" in scenarios["no-causal-uplift-or-safety-approval"]["expectedBehavior"]


def test_oracle_recomputes_when_observations_change(demo, tables, scenarios):
    changed = copy.deepcopy(tables)
    observation = next(
        row
        for row in changed["ProductionDay"]
        if row["WellId"] == "FIC-CS-01" and row["ProductionDate"] == "2026-07-09T00:00:00Z"
    )
    observation["OilStb"] += 31
    recalculated = {scenario["id"]: scenario for scenario in demo.build_scenarios(changed)}
    before = next(
        row
        for row in scenarios["monthly-volume-rates-coverage"]["expected"]
        if row["WellId"] == "FIC-CS-01" and row["Month"] == "2026-07"
    )
    after = next(
        row
        for row in recalculated["monthly-volume-rates-coverage"]["expected"]
        if row["WellId"] == "FIC-CS-01" and row["Month"] == "2026-07"
    )
    assert after["ObservedOilStb"] - before["ObservedOilStb"] == pytest.approx(31)
    assert after["ObservedOilPerCalendarDay"] - before["ObservedOilPerCalendarDay"] == pytest.approx(1)


@pytest.mark.parametrize(
    ("table", "key", "value", "message"),
    [
        ("Well", "FieldId", "DOES-NOT-EXIST", "dangling identifier"),
        ("Wellbore", "TotalDepthMdM", 1.0, "Measured depth"),
        ("Wellbore", "TotalDepthTvdssM", 10.0, "TVDSS datum"),
        ("LogInterval", "PorosityFraction", 1.2, "Invalid log fraction"),
        ("Completion", "BaseMdM", 99999.0, "outside measured borehole depth"),
        ("ProductionDay", "OperatingHours", 25.0, "operating hours"),
        ("ProductionDay", "OilStb", -1.0, "daily volume"),
        ("ProductionDay", "RecordedAt", "2026-09-02T00:00:00Z", "as-of"),
        ("ProductionDay", "PeriodStartUtc", "2026-06-01T00:00:00Z", "WIB production calendar"),
        ("SeismicInterpretation", "TopDepthQ10TvdssM", 99999.0, "quantiles"),
    ],
)
def test_validator_rejects_inconsistent_facts(demo, tables, table, key, value, message):
    changed = copy.deepcopy(tables)
    changed[table][0][key] = value
    with pytest.raises(ValueError, match=message):
        demo.validate_tables(changed)


def test_validator_rejects_null_coercion_duplicates_and_bridge_fks(demo, tables):
    changed = copy.deepcopy(tables)
    next(row for row in changed["ProductionDay"] if row["Quality"] == "Missing")["OilStb"] = 0.0
    with pytest.raises(ValueError, match="Missing days"):
        demo.validate_tables(changed)
    changed = copy.deepcopy(tables)
    changed["DocumentWell"][0]["WellId"] = "UNKNOWN"
    with pytest.raises(ValueError, match="dangling identifier"):
        demo.validate_tables(changed)
    changed = copy.deepcopy(tables)
    duplicate = {**changed["ProductionDay"][0], "ProductionDayId": "unique-id-same-date"}
    changed["ProductionDay"].append(duplicate)
    with pytest.raises(ValueError, match="Duplicate canonical"):
        demo.validate_tables(changed)


def test_bundle_csv_sqlite_hashes_determinism_and_no_oracle_leakage(demo, tables, output_root):
    first, second = output_root / "first", output_root / "second"
    manifest = demo.write_bundle(first)
    second_manifest = demo.write_bundle(second)
    assert manifest == second_manifest
    assert manifest["tableCounts"] == {name: len(rows) for name, rows in tables.items()}
    assert manifest["rowCounts"] == manifest["tableCounts"]
    assert manifest["scenarioCount"] == 12 and manifest["documentCount"] == 15
    assert len(manifest["provenance"]["sources"]) == 7
    assert manifest["evaluationOnly"] == ["evaluation/scenarios.json", "evaluation/oracle.sqlite"]
    for item in manifest["files"]:
        content = (first / item["path"]).read_bytes()
        assert content == (second / item["path"]).read_bytes()
        assert item["bytes"] == len(content)
        assert item["sha256"] == hashlib.sha256(content).hexdigest()
    source_files = [first / "schema.json", first / "documents" / "source-documents.json", *(first / "tables").iterdir()]
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        assert '"expected"' not in text and '"expectedBehavior"' not in text and '"requiredTools"' not in text
    for name, rows in tables.items():
        exported = [json.loads(line) for line in (first / "tables" / f"{name}.jsonl").read_text().splitlines()]
        assert exported == rows
    with (first / "tables" / "ProductionDay.csv").open(newline="", encoding="utf-8") as stream:
        csv_rows = list(csv.DictReader(stream))
    missing = next(row for row in csv_rows if row["Quality"] == "Missing")
    assert missing["OilStb"] == missing["OperatingHours"] == ""
    assert csv_rows[0]["Synthetic"] == "true"
    oracle = sqlite3.connect(first / "evaluation" / "oracle.sqlite")
    try:
        oracle.row_factory = sqlite3.Row
        assert oracle.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {row["name"] for row in oracle.execute("SELECT name FROM sqlite_master WHERE type='table'")} == set(
            tables
        )
        for scenario in json.loads((first / "evaluation" / "scenarios.json").read_text()):
            assert scenario["sql"].lstrip().upper().startswith(("SELECT", "WITH"))
            assert [dict(row) for row in oracle.execute(scenario["sql"])] == scenario["expected"]
        assert oracle.execute("SELECT typeof(Synthetic) FROM Well LIMIT 1").fetchone()[0] == "integer"
        assert (
            oracle.execute("SELECT typeof(OilStb) FROM ProductionDay WHERE Quality='Missing'").fetchone()[0] == "null"
        )
        oracle.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            with oracle:
                oracle.execute("UPDATE DocumentWell SET WellId='NO-SUCH-WELL' WHERE rowid=1")
    finally:
        oracle.close()


def test_bundle_refuses_implicit_overwrite_and_symlinks(demo, output_root):
    output = output_root / "bundle"
    original = demo.write_bundle(output)
    with pytest.raises(FileExistsError, match="overwrite"):
        demo.write_bundle(output)
    assert demo.write_bundle(output, overwrite=True) == original
    linked_output = output_root / "linked"
    linked_output.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        demo.write_bundle(linked_output, overwrite=True)
    (output / "schema.json").unlink()
    (output / "schema.json").symlink_to(output / "manifest.json")
    manifest_before = (output / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="symlink"):
        demo.write_bundle(output, overwrite=True)
    assert (output / "manifest.json").read_bytes() == manifest_before


def test_bundle_regeneration_preserves_compiler_and_renderer_outputs(demo, output_root):
    output = output_root / "bundle"
    demo.write_bundle(output)
    preserved = {
        "fabric/compiled-definition.json": b'{"compiler-owned":true}',
        "documents/pdf/d-base.pdf": b"renderer-owned PDF",
        "documents/pdf/citation-map.json": b'{"renderer-owned":true}',
        "evaluation/demo-guide.pdf": b"renderer-owned guide",
    }
    for relative, content in preserved.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    demo.write_bundle(output, overwrite=True)
    assert all((output / relative).read_bytes() == content for relative, content in preserved.items())


@pytest.mark.parametrize("identifier", REVIEWED_TASKS)
def test_primary_scenarios_use_exact_reviewed_user_tasks(demo, scenarios, identifier):
    row = scenarios[identifier]
    persona, task, question = REVIEWED_TASKS[identifier]
    assert (row["persona"], row["userTask"], row["question"]) == (persona, task, question)
    assert row["questionVersion"] == 2
    assert isinstance(row["documentTextRequired"], bool)
    assert isinstance(row["documentContextNote"], str) and row["documentContextNote"]
    assert not re.search(r"\b(graph|ontology|fabric|gql|sql)\b", row["question"], re.IGNORECASE)
    assert not any(bait in row["question"].lower() for bait in ("tanpa menggandakan", "buktikan", "setujui"))
    previous = next(item for item in demo._technical_scenario_specs() if item["id"] == identifier)
    assert row["operatorPrompt"] == previous["question"]
    assert row["operatorPrompt"] != row["question"]


def test_reframing_preserves_all_twelve_ids_sql_evidence_and_43_oracle_rows(scenarios):
    rows = list(scenarios.values())
    assert len(rows) == len({row["id"] for row in rows}) == 12
    assert sum(len(row["expected"]) for row in rows) == 43
    frozen = [{key: row[key] for key in PRESERVED_SCENARIO_KEYS} for row in rows]
    checksum = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert checksum == V1_ORACLE_CONTRACT_SHA256


def test_document_requirements_are_explicit_and_grounded_behavior_is_preserved(demo, scenarios):
    required = {
        "uncompleted-log-screening",
        "allocation-report-revision",
        "connectivity-uncertain-normalize-pressure",
        "no-causal-uplift-or-safety-approval",
    }
    assert {identifier for identifier, row in scenarios.items() if row["documentTextRequired"]} == required
    assert (
        "displaying all 24 raw oracle rows is not required"
        in scenarios["monthly-volume-rates-coverage"]["expectedBehavior"]
    )
    assert (
        "ask for the note or cutoffs rather than inventing thresholds"
        in scenarios["uncompleted-log-screening"]["expectedBehavior"]
    )
    assert "Refuse quantified causal uplift" in scenarios["no-causal-uplift-or-safety-approval"]["expectedBehavior"]
    for previous in demo._technical_scenario_specs():
        row = scenarios[previous["id"]]
        if previous["id"] not in {"monthly-volume-rates-coverage", "uncompleted-log-screening"}:
            assert row["expectedBehavior"] == previous["expectedBehavior"]
        if previous["id"] == "uncompleted-log-screening":
            assert row["expectedBehavior"].startswith(previous["expectedBehavior"])


def test_handout_contains_only_end_user_tasks_and_four_attachment_requirements(demo, tables, scenarios):
    rows = list(scenarios.values())
    handout = demo.build_questions_handout(rows)
    assert "Cutoff dataset: 1 September 2026" in handout
    assert all(code in handout for code in ("FIC-CS-xx", "FIC-SS-xx", "FIC-JV-xx", "SS02 = FIC-SS-02"))
    assert "bukan bukti hasil wawancara pengguna" in handout
    assert handout.count("Lampiran wajib:") == 4
    assert not re.search(r"\b(graph|ontology|fabric|gql|sql)\b", handout, re.IGNORECASE)
    assert not any(key in handout for key in ("operatorPrompt", "expected", "requiredTools"))
    sources = json.dumps({"tables": tables, "documents": demo.build_documents(tables)}, ensure_ascii=False)
    for index, row in enumerate(rows, 1):
        assert f"{index}. {row['persona']} — {row['userTask']}\n{row['question']}" in handout
        assert handout.count(row["question"]) == 1
        assert row["operatorPrompt"] not in handout
        assert row["sql"] not in handout
        assert row["question"] not in sources
        assert row["operatorPrompt"] not in sources
        if row["documentTextRequired"]:
            assert "Lampiran wajib: " + ", ".join(row["requiredDocumentIds"]) + "." in handout


def _write_v1_pack(demo, output):
    demo.write_bundle(output)
    current = json.loads((output / "evaluation" / "scenarios.json").read_text(encoding="utf-8"))
    technical = demo._technical_scenario_specs()
    for old, new in zip(technical, current, strict=True):
        old["sql"] = old["sql"].strip()
        old["expected"] = new["expected"]
    original_bytes = json.dumps(technical, ensure_ascii=False, indent=4).encode("utf-8")
    (output / "evaluation" / "scenarios.json").write_bytes(original_bytes)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["files"] if row["path"] == "evaluation/scenarios.json")
    entry["bytes"] = len(original_bytes)
    entry["sha256"] = hashlib.sha256(original_bytes).hexdigest()
    (output / "manifest.json").write_text(demo._json(manifest), encoding="utf-8")
    return original_bytes


def test_questions_only_refresh_preserves_v1_and_every_source_artifact(demo, output_root):
    output = output_root / "bundle"
    original_bytes = _write_v1_pack(demo, output)
    protected = {
        "fabric/compiled-definition.json": b'{"compiler-owned":true}',
        "documents/pdf/d-base.pdf": b"renderer-owned PDF",
        "evaluation/demo-guide.pdf": b"previous guide",
        "evaluation/live-gql-report.json": b'{"previous-live-run":true}',
    }
    for relative, content in protected.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    manifest_before = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    demo.refresh_scenario_questions(output)
    after = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert set(after) - set(before) == {"evaluation/scenarios-technical-v1.json", "evaluation/questions-end-user.txt"}
    assert all(
        after[name] == digest
        for name, digest in before.items()
        if name not in {"evaluation/scenarios.json", "manifest.json"}
    )
    archive = output / "evaluation" / "scenarios-technical-v1.json"
    assert archive.read_bytes() == original_bytes
    current_bytes = (output / "evaluation" / "scenarios.json").read_bytes()
    updated = json.loads(current_bytes)
    previous = json.loads(original_bytes)
    for old, new in zip(previous, updated, strict=True):
        assert all(old[key] == new[key] for key in PRESERVED_SCENARIO_KEYS)
        assert old["question"] == new["operatorPrompt"]
    manifest_expected = copy.deepcopy(manifest_before)
    entry = next(row for row in manifest_expected["files"] if row["path"] == "evaluation/scenarios.json")
    entry["bytes"] = len(current_bytes)
    entry["sha256"] = hashlib.sha256(current_bytes).hexdigest()
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == manifest_expected
    assert (output / "evaluation" / "questions-end-user.txt").read_text(
        encoding="utf-8"
    ) == demo.build_questions_handout(updated)
    demo.refresh_scenario_questions(output)
    assert archive.read_bytes() == original_bytes
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in after.items())


def test_questions_only_refresh_rejects_source_drift_before_writing(demo, output_root):
    output = output_root / "bundle"
    original_bytes = _write_v1_pack(demo, output)
    changed_source = output / "tables" / "WellTest.csv"
    changed_source.write_bytes(changed_source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Source artifact changed"):
        demo.refresh_scenario_questions(output)
    assert (output / "evaluation" / "scenarios.json").read_bytes() == original_bytes
    assert not (output / "evaluation" / "scenarios-technical-v1.json").exists()
    assert not (output / "evaluation" / "questions-end-user.txt").exists()


def test_questions_only_refresh_rejects_answer_drift_before_writing(demo, output_root):
    output = output_root / "bundle"
    _write_v1_pack(demo, output)
    scenarios_path = output / "evaluation" / "scenarios.json"
    altered = json.loads(scenarios_path.read_text(encoding="utf-8"))
    altered[0]["expected"][0]["ObservedOilStb"] += 1
    scenarios_path.write_text(demo._json(altered), encoding="utf-8")
    before = scenarios_path.read_bytes()
    with pytest.raises(ValueError, match="Scenario oracle or evidence contract changed"):
        demo.refresh_scenario_questions(output)
    assert scenarios_path.read_bytes() == before
    assert not (output / "evaluation" / "scenarios-technical-v1.json").exists()
