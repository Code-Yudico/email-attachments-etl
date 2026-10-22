"""Build the synthetic attachments the pipeline consumes.

These stand in for the reports a supplier system emails every morning. The
content is invented; what is reproduced is the shape that makes the pipeline
necessary:

* a pipe-separated text file in latin-1, with accented branch names
* an Excel workbook
* upper-case Spanish headers that have to be mapped to the destination columns
* the text report arrives zipped, as the real ones often do

    python scripts/generate_data.py --date 2026-10-20
"""

from __future__ import annotations

import argparse
import io
import random
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "outbox"

BRANCHES = [
    ("SUC001", "Centro Histórico", "Centro"),
    ("SUC002", "León Plaza", "Bajío"),
    ("SUC003", "Mérida Norte", "Sureste"),
    ("SUC004", "Cancún Zona Hotelera", "Sureste"),
    ("SUC005", "Monterrey Valle", "Norte"),
]

PRODUCTS = [
    ("P-100", "Audífonos inalámbricos"),
    ("P-200", "Teclado mecánico"),
    ("P-300", "Monitor 27 pulgadas"),
    ("P-400", "Batería portátil"),
    ("P-500", "Cámara web HD"),
]

CHANNELS = ["Mostrador", "Telefónico", "En línea"]
MOVEMENT_TYPES = ["Entrada", "Salida", "Ajuste"]
WAREHOUSES = ["Principal", "Secundario"]


def sales_frame(report_date: date, rows: int = 60) -> pd.DataFrame:
    faker = Faker("es_MX")
    records = []
    for _ in range(rows):
        branch_code, branch_name, region = random.choice(BRANCHES)
        product_code, product_name = random.choice(PRODUCTS)
        units = random.randint(1, 25)
        unit_price = round(random.uniform(180, 7500), 2)
        net = round(units * unit_price, 2)
        discount = round(net * random.choice([0, 0, 0.05, 0.1]), 2)
        records.append(
            {
                "FECHA": report_date.strftime("%d/%m/%Y"),
                "CVE_SUCURSAL": branch_code,
                "NOM_SUCURSAL": branch_name,
                "REGION": region,
                "CVE_PRODUCTO": product_code,
                "DES_PRODUCTO": product_name,
                "CANAL": random.choice(CHANNELS),
                "UNIDADES": units,
                "IMPORTE_S_IMP": net,
                "IMPORTE_C_IMP": round(net * 1.16, 2),
                "DESCUENTO": discount,
                "VENDEDOR": faker.name(),
            }
        )
    return pd.DataFrame(records)


def inventory_frame(report_date: date, rows: int = 40) -> pd.DataFrame:
    records = []
    for _ in range(rows):
        branch_code, _, _ = random.choice(BRANCHES)
        product_code, product_name = random.choice(PRODUCTS)
        records.append(
            {
                "FECHA": report_date.strftime("%d/%m/%Y"),
                "CVE_SUCURSAL": branch_code,
                "CVE_PRODUCTO": product_code,
                "DES_PRODUCTO": product_name,
                "TIPO_MOVIMIENTO": random.choice(MOVEMENT_TYPES),
                "CANTIDAD": random.randint(1, 120),
                "EXISTENCIA_FINAL": random.randint(0, 900),
                "ALMACEN": random.choice(WAREHOUSES),
            }
        )
    return pd.DataFrame(records)


def build_attachments(report_date: date) -> dict[str, list[tuple[str, bytes]]]:
    """Attachments per report subject, as (filename, content) pairs."""
    stamp = report_date.isoformat()

    # The sales report is delimited, latin-1 and zipped.
    text_name = f"ventas_sucursal_{stamp}.txt"
    text_bytes = sales_frame(report_date).to_csv(
        index=False, sep="|", encoding="latin-1"
    ).encode("latin-1")

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(text_name, text_bytes)

    excel = io.BytesIO()
    inventory_frame(report_date).to_excel(excel, index=False)

    return {
        "Reporte diario de ventas": [
            (f"ventas_sucursal_{stamp}.zip", archive.getvalue())
        ],
        "Movimientos de inventario": [
            (f"inventario_{stamp}.xlsx", excel.getvalue())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the synthetic attachments.")
    parser.add_argument(
        "--date",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=date.today() - timedelta(days=1),
        help="report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    parser.add_argument("--seed", type=int, default=42, help="seed, for reproducible output")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="output directory")
    args = parser.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"Attachments for {args.date.isoformat()}:")
    for subject, files in build_attachments(args.date).items():
        for filename, content in files:
            (args.out / filename).write_bytes(content)
            print(f"  {filename}  ({len(content):,} bytes)  <- {subject}")


if __name__ == "__main__":
    main()
