"""Build operator-only GQL cases with independently computed SQLite expectations."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import cast

SQL_PARAMETERS = {"periodStart": "2026-08-01", "periodEnd": "2026-09-01"}
GQL_WINDOW = (
    "p.ProductionDate >= ZONED_DATETIME('2026-08-01T00:00:00Z') "
    "AND p.ProductionDate < ZONED_DATETIME('2026-09-01T00:00:00Z')"
)


def specifications() -> list[tuple[str, str, str]]:
    return [
        (
            "registry-wells-by-field",
            "MATCH (w:Well)-[:well_belongs_to_field]->(f:Field) "
            "RETURN f.Name AS fieldName, count(DISTINCT w.WellId) AS wells GROUP BY fieldName ORDER BY fieldName",
            "SELECT f.Name AS fieldName, COUNT(DISTINCT w.WellId) AS wells FROM Well w "
            "JOIN Field f ON f.FieldId=w.FieldId GROUP BY f.FieldId ORDER BY fieldName",
        ),
        (
            "august-fact-grain",
            f"MATCH (p:ProductionDay WHERE {GQL_WINDOW}) "
            "RETURN count(*) AS days, count(DISTINCT p.ProductionDayId) AS distinctDays, "
            "count(p.OilStb) AS observedDays",
            "SELECT COUNT(*) AS days,COUNT(DISTINCT p.ProductionDayId) AS distinctDays, "
            "COUNT(p.OilStb) AS observedDays FROM ProductionDay p "
            "WHERE p.ProductionDate>=:periodStart AND p.ProductionDate<:periodEnd",
        ),
        (
            "august-oil-by-well",
            f"MATCH (p:ProductionDay WHERE {GQL_WINDOW})-[:production_day_observes_well]->(w:Well) "
            "RETURN w.WellId AS wellId, sum(p.OilStb) AS oilStb, sum(p.OperatingHours) AS hours, "
            "count(*) AS calendarDays, count(p.OilStb) AS observedDays "
            "GROUP BY wellId ORDER BY wellId",
            "SELECT w.WellId AS wellId,SUM(p.OilStb) AS oilStb,SUM(p.OperatingHours) AS hours, "
            "COUNT(*) AS calendarDays,COUNT(p.OilStb) AS observedDays "
            "FROM ProductionDay p JOIN Well w ON w.WellId=p.WellId "
            "WHERE p.ProductionDate>=:periodStart AND p.ProductionDate<:periodEnd "
            "GROUP BY w.WellId ORDER BY wellId",
        ),
        (
            "seroja-weighted-watercut",
            f"MATCH (p:ProductionDay WHERE {GQL_WINDOW})-[:production_day_observes_well]->(w:Well)"
            "-[:well_belongs_to_field]->(f:Field WHERE f.FieldId = 'FIC-CS') "
            "RETURN sum(p.OilStb) AS oilStb, sum(p.WaterBbl) AS waterBbl, "
            "100.0 * sum(p.WaterBbl) / (sum(p.OilStb) + sum(p.WaterBbl)) AS watercutPercent",
            "SELECT SUM(p.OilStb) AS oilStb,SUM(p.WaterBbl) AS waterBbl,"
            "100.0*SUM(p.WaterBbl)/(SUM(p.OilStb)+SUM(p.WaterBbl)) AS watercutPercent "
            "FROM ProductionDay p JOIN Well w ON w.WellId=p.WellId "
            "WHERE w.FieldId='FIC-CS' AND p.ProductionDate>=:periodStart AND p.ProductionDate<:periodEnd",
        ),
        (
            "rimba-cgr-wgr",
            f"MATCH (p:ProductionDay WHERE {GQL_WINDOW})-[:production_day_observes_well]->(w:Well)"
            "-[:well_belongs_to_field]->(f:Field WHERE f.FieldId = 'FIC-SS') "
            "RETURN sum(p.GasMMscf) AS gasMMscf, "
            "sum(p.CondensateStb) / sum(p.GasMMscf) AS cgrStbPerMMscf, "
            "sum(p.WaterBbl) / sum(p.GasMMscf) AS wgrBblPerMMscf",
            "SELECT SUM(p.GasMMscf) AS gasMMscf,SUM(p.CondensateStb)/SUM(p.GasMMscf) AS cgrStbPerMMscf,"
            "SUM(p.WaterBbl)/SUM(p.GasMMscf) AS wgrBblPerMMscf FROM ProductionDay p "
            "JOIN Well w ON w.WellId=p.WellId "
            "WHERE w.FieldId='FIC-SS' AND p.ProductionDate>=:periodStart AND p.ProductionDate<:periodEnd",
        ),
        (
            "missing-is-not-zero",
            "MATCH (p:ProductionDay WHERE p.Quality = 'Missing') "
            "RETURN p.WellId AS wellId, p.OilStb AS oilStb, p.OperatingHours AS hours, p.Quality AS quality",
            "SELECT WellId AS wellId,OilStb AS oilStb,OperatingHours AS hours,Quality AS quality "
            "FROM ProductionDay WHERE Quality='Missing'",
        ),
        (
            "known-shutdown-days",
            "MATCH (p:ProductionDay WHERE p.Quality = 'ShutdownValidated') "
            "RETURN p.WellId AS wellId, count(*) AS days, sum(p.OilStb) AS oilStb, "
            "sum(p.OperatingHours) AS hours GROUP BY wellId ORDER BY wellId",
            "SELECT WellId AS wellId,COUNT(*) AS days,SUM(OilStb) AS oilStb,SUM(OperatingHours) AS hours "
            "FROM ProductionDay WHERE Quality='ShutdownValidated' GROUP BY WellId ORDER BY wellId",
        ),
        (
            "deepest-measured-depth",
            "MATCH (b:Wellbore) RETURN b.WellboreId AS boreId,b.TotalDepthMdM AS mdM,"
            "b.TotalDepthTvdM AS tvdM,b.TotalDepthTvdssM AS tvdssM ORDER BY mdM DESC, boreId LIMIT 1",
            "SELECT WellboreId AS boreId,TotalDepthMdM AS mdM,TotalDepthTvdM AS tvdM,"
            "TotalDepthTvdssM AS tvdssM FROM Wellbore ORDER BY mdM DESC,boreId LIMIT 1",
        ),
        (
            "deepest-vertical-depth",
            "MATCH (b:Wellbore) RETURN b.WellboreId AS boreId,b.TotalDepthMdM AS mdM,"
            "b.TotalDepthTvdM AS tvdM,b.TotalDepthTvdssM AS tvdssM ORDER BY tvdM DESC, boreId LIMIT 1",
            "SELECT WellboreId AS boreId,TotalDepthMdM AS mdM,TotalDepthTvdM AS tvdM,"
            "TotalDepthTvdssM AS tvdssM FROM Wellbore ORDER BY tvdM DESC,boreId LIMIT 1",
        ),
        (
            "commingled-total-not-per-zone",
            f"MATCH (p:ProductionDay WHERE {GQL_WINDOW} AND p.WellId = 'FIC-CS-04') "
            "RETURN sum(p.OilStb) AS oilStb, count(p.OilStb) AS observedDays",
            "SELECT SUM(p.OilStb) AS oilStb,COUNT(p.OilStb) AS observedDays FROM ProductionDay p "
            "WHERE p.WellId='FIC-CS-04' AND p.ProductionDate>=:periodStart AND p.ProductionDate<:periodEnd",
        ),
        (
            "commingled-active-reservoirs-separate",
            "MATCH (c:Completion WHERE c.EffectiveFrom <= ZONED_DATETIME('2026-08-31T00:00:00Z') "
            "AND (c.EffectiveTo IS NULL OR c.EffectiveTo > ZONED_DATETIME('2026-08-31T00:00:00Z')))"
            "-[:completion_in_wellbore]->(b:Wellbore)"
            "-[:wellbore_belongs_to_well]->(w:Well WHERE w.WellId = 'FIC-CS-04') "
            "RETURN count(DISTINCT c.ReservoirId) AS reservoirs",
            "SELECT COUNT(DISTINCT c.ReservoirId) AS reservoirs FROM Completion c "
            "JOIN Wellbore b ON b.WellboreId=c.WellboreId WHERE b.WellId='FIC-CS-04' "
            "AND c.EffectiveFrom<='2026-08-31T00:00:00Z' "
            "AND (c.EffectiveTo IS NULL OR c.EffectiveTo>'2026-08-31T00:00:00Z')",
        ),
        (
            "all-co2-evidence-not-largest-value",
            "MATCH (s:FluidSample WHERE s.WellId = 'FIC-SS-02')-[:fluid_sample_supported_by_document]->(d:Document) "
            "RETURN s.FluidSampleId AS sampleId,s.GasCo2MolPercent AS co2MolPercent,"
            "s.ReportStatus AS reportStatus,d.DocumentId AS docId,d.Status AS docStatus ORDER BY sampleId",
            "SELECT s.FluidSampleId AS sampleId,s.GasCo2MolPercent AS co2MolPercent,"
            "s.ReportStatus AS reportStatus,d.DocumentId AS docId,d.Status AS docStatus "
            "FROM FluidSample s JOIN Document d ON d.DocumentId=s.DocumentId "
            "WHERE s.WellId='FIC-SS-02' ORDER BY sampleId",
        ),
        (
            "reservoir-pressure-common-datum",
            "MATCH (s:FluidSample WHERE s.ReservoirId = 'R-SS-1' AND s.ReportStatus = 'Approved')"
            "-[:fluid_sample_from_reservoir]->(r:Reservoir) "
            "RETURN s.WellId AS wellId,s.StaticPressurePsia + s.PressureGradientPsiPerM * "
            "(r.PressureReferenceTvdssM - s.PressureDatumTvdssM) AS referencePsia ORDER BY wellId",
            "SELECT s.WellId AS wellId,s.StaticPressurePsia+s.PressureGradientPsiPerM*"
            "(r.PressureReferenceTvdssM-s.PressureDatumTvdssM) AS referencePsia "
            "FROM FluidSample s JOIN Reservoir r ON r.ReservoirId=s.ReservoirId "
            "WHERE s.ReservoirId='R-SS-1' AND s.ReportStatus='Approved' ORDER BY wellId",
        ),
    ]


def build_cases(connection: sqlite3.Connection) -> list[dict[str, object]]:
    connection.row_factory = sqlite3.Row
    return [
        {
            "id": case_id,
            "query": gql,
            "expected": [dict(row) for row in connection.execute(sql, SQL_PARAMETERS)],
            "oracleSql": sql,
            "oracleParameters": SQL_PARAMETERS,
            "executionState": "not-run-against-energy-graph",
        }
        for case_id, gql, sql in specifications()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    pack = cast(Path, args.pack)
    output = pack / "evaluation" / "gql-cases.json"
    if output.exists() and not args.overwrite:
        raise FileExistsError("GQL cases already exist; pass --overwrite to recompute them")
    database = pack / "evaluation" / "oracle.sqlite"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        cases = build_cases(connection)
    output.write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} GQL cases to {output}; expectations computed from the read-only SQLite oracle.")


if __name__ == "__main__":
    main()
