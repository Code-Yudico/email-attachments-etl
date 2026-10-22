# ETL de adjuntos de correo a SQL Server

*[English version](README.en.md)*

Pipeline ETL en Python que lee los reportes que llegan como adjuntos a un buzón, los normaliza con pandas y los carga en SQL Server. Es **incremental**: cada correo procesado queda registrado, y la siguiente corrida solo pide lo que llegó después.

> **Sobre esta versión pública.** Está basado en un ETL que escribí para mi trabajo. La versión original lee Outlook a través de COM (`win32com`), lo que lo ata a un escritorio Windows con Outlook instalado y con sesión abierta; aquí la fuente es **IMAP**, que es el mismo trabajo sobre un protocolo estándar y permite que el proyecto corra en cualquier máquina. Los datos son **sintéticos** y el `docker-compose` incluye un servidor de correo de prueba, así que el pipeline se puede ejecutar completo sin acceso a ningún buzón real.

---

## El problema

Varios reportes operativos llegaban por correo todas las mañanas, cada uno con su formato: uno como texto separado por `|` en latin-1 y comprimido en ZIP, otro como Excel. Alguien tenía que abrir el correo, descargar el adjunto, descomprimirlo, limpiarlo y cargarlo a la base. Eso significaba:

- **Trabajo manual repetitivo** antes de poder consultar nada.
- **Riesgo de cargar dos veces o saltarse un día**, porque el control de qué ya se había cargado vivía en la memoria de quien lo hacía.
- **Un cuello de botella por cada reporte nuevo**: agregar uno implicaba escribir otro script.

## La solución

```mermaid
flowchart LR
    subgraph Origen
        A["Buzón IMAP<br/><i>un asunto por reporte</i>"]
    end

    subgraph Pipeline["Pipeline Python"]
        direction TB
        B["<b>Leer</b><br/>busca por asunto<br/>desde la última carga"]
        C["<b>Adjuntos</b><br/>guarda · descomprime ZIP"]
        D["<b>Transformar</b><br/>mapea columnas · tipos"]
        E["<b>Cargar</b><br/>append por lotes"]
        B --> C --> D --> E
    end

    subgraph Destino["SQL Server"]
        F[("branch_sales")]
        G[("inventory_movements")]
        H[("etl_load_history")]
    end

    Y["config/reports.yaml"]

    A --> B
    Y -. "define cada reporte" .-> B
    Y -. "" .-> D
    E --> F
    E --> G
    E == "registra la carga" ==> H
    H == "marca de agua" ==> B
```

## Decisiones de diseño

**La marca de agua vive en la base, no en el disco ni en la memoria.** Cada adjunto cargado se registra en `etl_load_history` con el asunto bajo el que llegó y la fecha de recepción del correo. Al arrancar, el pipeline recupera el último correo cargado **por asunto** y le pide al buzón solo lo posterior:

```sql
WITH ranked AS (
    SELECT subject, received_at,
           ROW_NUMBER() OVER (PARTITION BY subject ORDER BY received_at DESC) AS rn
    FROM dbo.etl_load_history
)
SELECT subject, received_at FROM ranked WHERE rn = 1;
```

Como la marca avanza al registrar cada adjunto, una corrida que muere a la mitad retoma desde el último que realmente cargó, no desde el principio. Y un asunto que nunca se ha visto arranca desde una ventana acotada (`INITIAL_LOOKBACK_DAYS`) en vez de leer el buzón entero.

**Agregar un reporte es editar un YAML, no escribir código.** [`config/reports.yaml`](config/reports.yaml) declara, por reporte: el asunto a buscar, qué adjuntos reconocer y cómo leerlos (formato, encoding, separador), el mapeo de columnas de origen a destino, los tipos y la tabla destino. El pipeline no cambia.

**El filtro se aplica en el servidor y otra vez en el cliente.** IMAP solo filtra fechas con resolución de día, así que se pide al servidor un día de más y el corte exacto se aplica contra la marca de tiempo de cada correo. Filtrar primero en el servidor mantiene pequeña la descarga.

**Los adjuntos se guardan con el timestamp del correo como prefijo**, para que dos entregas del mismo nombre no se pisen. Pero los patrones del YAML se comparan contra el **nombre original** del archivo: la configuración describe el archivo como llega, no cómo este pipeline decide almacenarlo.

**Cada entrada de un ZIP se reduce a su nombre base antes de escribirla.** Un ZIP malicioso puede traer nombres como `../../algo`, y respetarlos permitiría escribir fuera de la carpeta de trabajo.

**Un adjunto ilegible no tumba la corrida ni mueve la marca de agua.** Se registra, se omite y los demás siguen. Pero si a un adjunto le falta una columna esperada, eso **sí** es un error: cargar en silencio un reporte truncado es peor que fallar.

**Las fechas se normalizan a UTC sin zona horaria.** `DATETIME2` no guarda offset, y basta con que un solo valor llegue con zona para que la comparación contra la marca de agua reviente.

## Cómo correrlo

**Requisitos:** Python 3.10+, [Docker](https://www.docker.com/products/docker-desktop/) y el [ODBC Driver 18 para SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server).

```bash
docker compose up -d                    # SQL Server + servidor de correo de prueba
cp .env.example .env                    # Windows: copy .env.example .env

python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt

python scripts/seed_mailbox.py --days 3 # entrega 3 días de reportes al buzón
python -m src.pipeline
```

El pipeline crea la base y la tabla de control si no existen.

### La parte que vale la pena ver

Córrelo una segunda vez sin enviar nada nuevo:

```
Read 2 watermark(s) from the control table
Reporte diario de ventas: 0 new email(s)
Movimientos de inventario: 0 new email(s)
  0 email(s), 0 attachment(s), 0 row(s)
```

Ahora simula que llega el reporte del día siguiente y vuelve a correrlo:

```bash
python scripts/seed_mailbox.py --days 1
python -m src.pipeline
```

```
Reporte diario de ventas: 1 new email(s)
  branch_sales: 60 row(s) loaded
Movimientos de inventario: 1 new email(s)
  inventory_movements: 40 row(s) loaded
  2 email(s), 2 attachment(s), 100 row(s)
```

Solo lo nuevo. Para verlo desde la base:

```sql
SELECT subject, received_at, attachment, rows_loaded
FROM etl_load_history ORDER BY received_at;
```

### Pruebas

```bash
pytest
```

## Estructura

```
config/reports.yaml   definición declarativa de cada reporte
src/
  config.py       configuración desde variables de entorno
  reports.py      carga y valida el YAML
  mailbox.py      lectura del buzón por IMAP
  attachments.py  guardado de adjuntos y extracción de ZIP
  transform.py    mapeo de columnas y tipos
  control.py      tabla de control y marca de agua incremental
  db.py           motor SQLAlchemy, reintentos, creación de la base
  load.py         carga a la tabla destino
  pipeline.py     orquestación
scripts/
  generate_data.py   genera los adjuntos sintéticos
  seed_mailbox.py    los entrega al buzón de prueba por SMTP
sql/schema.sql       DDL de la tabla de control, como referencia
tests/               pruebas de configuración, transformación y correo
```

## Limitaciones y qué mejoraría

- **Las tablas de reportes las crea `to_sql`**, que infiere los tipos: las columnas de texto quedan como `varchar(max)` y los importes como `float`. Funciona, pero `varchar(max)` impide indexar bien y los montos deberían ser `DECIMAL`. Escribiría el DDL a mano, como ya está el de la tabla de control.
- **La carga es `append` sin reproceso.** La marca de agua garantiza que un correo se procesa una vez, pero si un reporte se reenvía corregido, hay que borrar a mano lo anterior. Un borrado por fecha antes de insertar lo resolvería.
- **No hay orquestador.** Esto corre con el Programador de tareas. Airflow o Prefect darían reintentos, alertas y linaje sin escribirlos a mano.
- **No hay pruebas de integración.** Las pruebas cubren configuración, transformación y manejo de correo y ZIP, pero no la carga real contra SQL Server ni la lectura IMAP, que hoy solo se verifican a mano con el `docker-compose`.
- **El buzón se lee, no se marca.** Los correos no se mueven ni se marcan como leídos, así que el buzón crece sin límite. En producción conviene archivarlos tras procesarlos.

---

Desarrollado con apoyo de IA (Claude); el problema, las decisiones de diseño y la validación son míos.

**José Francisco Yudico Martínez** — Profesional interdisciplinario en ciencia y análisis de datos.

Licencia MIT.
