<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo_fintself_dark.png">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo_fintself_light.png">
    <img alt="Fintself Logo" src="assets/logo_fintself_light.png" width="150">
  </picture>
</p>

<p align="center">
  <strong>Scraper de movimientos bancarios open source y colaborativo.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/fintself/"><img alt="PyPI" src="https://img.shields.io/pypi/v/fintself.svg"></a>
  <a href="https://github.com/fintself/fintself/actions/workflows/release.yml"><img alt="Build Status" src="https://github.com/fintself/fintself/actions/workflows/release.yml/badge.svg"></a>
  <a href="https://github.com/fintself/fintself/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://github.com/psf/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>
  <a href="https://pypistats.org/packages/fintself"><img alt="PyPI - Downloads" src="https://img.shields.io/pypi/dm/fintself"></a>
</p>

**Fintself** te permite automatizar la extracción de tus movimientos financieros desde diversas entidades bancarias, entregándote los datos en formatos estructurados como XLSX, CSV o JSON. Funciona tanto como una herramienta de línea de comandos (CLI) como una librería de Python para que la integres en tus propios proyectos.

## Características principales

- **Múltiples bancos**: Soporte para varios bancos, con una arquitectura diseñada para añadir nuevos fácilmente.
- **Formatos flexibles**: Exporta tus datos a `xlsx`, `csv` o `json`.
- **Doble interfaz**: Úsalo desde tu terminal con su potente CLI o impórtalo como librería en tus scripts de Python.
- **Seguridad primero**: Las credenciales se solicitan de forma segura y no se almacenan. Se recomienda el uso de variables de entorno.
- **Modo de depuración**: ¿Algo falla? Activa el modo debug para visualizar el proceso del scraper en tiempo real y guardar capturas de pantalla para un análisis fácil.
- **Open source**: Revisa, audita y mejora el código. ¡Las contribuciones son bienvenidas!

## Bancos soportados

Actualmente, Fintself soporta los siguientes scrapers para Chile:

| Banco / scraper | ID | Débito | Crédito | Notas |
| --- | --- | --- | --- | --- |
| Banco Santander | `cl_santander` | Sí | Sí | Cuentas y tarjetas en CLP/USD. |
| Banco de Chile | `cl_banco_chile` | Sí | Sí | Cuentas y tarjetas nacionales/internacionales. |
| Banco Estado (CuentaRUT) | `cl_estado` | Sí | No | Solo movimientos de CuentaRUT. |
| Banco Bice | `cl_bice` | Sí | Sí | Contribución de [@albertocintolesi](https://github.com/albertocintolesi). |
| Tarjeta Cencosud Scotiabank | `cl_cencosud` | No aplica | Sí | |

Para ver la lista actualizada directamente desde la herramienta, ejecuta `fintself list`.

## Instalación

Para instalar Fintself, solo necesitas tener Python 3.9+ y ejecutar el siguiente comando:

```bash
pip install fintself
```

## Uso

### Línea de comandos (CLI)

1.  **Listar bancos disponibles**:

    ```bash
    fintself list
    ```

2.  **Ejecutar un scraper**:

    ```bash
    fintself scrape <bank_id> --output-file movimientos.xlsx
    ```

    - **`<bank_id>`**: El identificador del banco (ej: `cl_santander`).
    - **`--output-file`**: El archivo donde se guardarán los resultados. El formato (xlsx, csv, json) se infiere de la extensión.

    El programa buscará las credenciales (`<BANK_ID>_USER` y `<BANK_ID>_PASSWORD`) en las variables de entorno. Si no las encuentra, las pedirá de forma interactiva.

    **Ejemplo:**

    ```bash
    # Usando prompts interactivos
    fintself scrape cl_santander --output-file santander.xlsx

    # Usando variables de entorno
    export CL_SANTANDER_USER="tu-rut"
    export CL_SANTANDER_PASSWORD="tu-clave"
    fintself scrape cl_santander --output-file santander.csv

    # Banco Estado (CuentaRUT)
    export CL_ESTADO_USER="tu-rut"
    export CL_ESTADO_PASSWORD="tu-clave"
    fintself scrape cl_estado --output-file cuenta_rut.xlsx --debug
    ```

### Uso como librería en Python

```python
from fintself import get_scraper, MovementModel
from fintself.core.exceptions import FintselfException
import pandas as pd

# Las credenciales se pueden pasar directamente o se leerán desde
# las variables de entorno si no se proveen.
USER = "tu-usuario"
PASSWORD = "tu-clave"
BANK_ID = "cl_santander"

try:
    # Obtener el scraper usando la factory
    scraper = get_scraper(BANK_ID)

    # Ejecutar el scraper
    movements: list[MovementModel] = scraper.scrape(user=USER, password=PASSWORD)

    # Convertir a DataFrame de Pandas
    if movements:
        df = pd.DataFrame([m.model_dump() for m in movements])
        print(df.head())
    else:
        print("No se encontraron movimientos.")

except FintselfException as e:
    print(f"Ocurrió un error controlado: {e.message}")
except Exception as e:
    print(f"Ocurrió un error inesperado: {e}")

```

## Depuración (Debugging)

Si un scraper falla o necesitas ver qué está pasando, puedes activar el modo de depuración. Esto ejecutará el navegador en modo visible (no headless) y guardará capturas de pantalla y el contenido HTML de los pasos clave del proceso.

Los archivos de depuración se guardan en el directorio `debug_output/<bank_id>/`.

### Desde la CLI

Añade el flag `--debug`:

```bash
fintself scrape cl_santander --output-file out.xlsx --debug
```

### Desde Python

Pasa el argumento `debug_mode=True` a la factory `get_scraper`:

```python
scraper = get_scraper("cl_santander", debug_mode=True)
movements = scraper.scrape(user=USER, password=PASSWORD)
```

## Modo de ejecución del navegador

Por defecto, Fintself ejecuta el navegador en **modo visible** (no headless), ya que algunos bancos tienen protecciones anti-bot que detectan navegadores sin interfaz gráfica. Esto garantiza la mejor compatibilidad con todos los bancos soportados.

### Modo headless (no recomendado)

Si necesitas ejecutar el navegador en modo headless (sin interfaz gráfica), puedes usar la opción `--headless`, pero ten en cuenta que **algunos bancos pueden no funcionar correctamente**.

```bash
fintself scrape cl_santander --output-file out.xlsx --headless
```

⚠️ **Advertencia**: Al usar `--headless`, verás un mensaje de advertencia indicando que algunos bancos pueden fallar. Si encuentras problemas, ejecuta el scraper sin esta opción (modo visible por defecto).

### Desde Python

```python
# Modo visible (por defecto, recomendado)
scraper = get_scraper("cl_santander")

# Modo headless (puede no funcionar con algunos bancos)
scraper = get_scraper("cl_santander", headless=True)

# Forzar modo visible explícitamente
scraper = get_scraper("cl_santander", headless=False)
```

### Variables de entorno

También puedes configurar el modo headless mediante la variable de entorno `SCRAPER_HEADLESS_MODE`:

```bash
# En tu archivo .env o terminal
export SCRAPER_HEADLESS_MODE=true  # Habilitar headless (no recomendado)
export SCRAPER_HEADLESS_MODE=false # Modo visible (por defecto)
```

## ⚠️ Descargo de responsabilidad

Este software se proporciona "tal cual", sin garantía de ningún tipo. Al utilizar Fintself, estás interactuando con sitios bancarios y manejando credenciales sensibles.

- **Usa Fintself bajo tu propio riesgo.** Los desarrolladores no se hacen responsables de ningún problema, pérdida de datos, bloqueo de cuentas o cualquier otro daño que pueda surgir de su uso.
- **La seguridad de tus credenciales es tu responsabilidad.** Recomendamos encarecidamente utilizar variables de entorno en lugar de escribir tus credenciales en scripts.
- Los scrapers pueden dejar de funcionar en cualquier momento si el banco actualiza el diseño de su sitio web.

## ¿Cómo contribuir?

¡Las contribuciones son el corazón de Fintself! Si quieres añadir un nuevo banco, corregir un bug o proponer una mejora, eres bienvenido. Por favor, lee nuestra [**Guía de Contribución**](CONTRIBUTING.md) para empezar.

## Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.
