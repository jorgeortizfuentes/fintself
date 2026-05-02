# Guía de Contribución para Fintself

¡Gracias por tu interés en contribuir a Fintself! Este documento establece las directrices para asegurar que el proyecto sea mantenible, colaborativo y de alta calidad.

## Filosofía del Proyecto

1.  **Seguridad Primero**: El manejo de credenciales es la máxima prioridad. Nunca se deben exponer datos sensibles.
2.  **Experiencia del Desarrollador (DX)**: El código debe ser claro, fácil de entender y de extender.
3.  **Pruebas Robustas**: Cada scraper debe ser probado de forma aislada y sin depender de servicios externos.
4.  **Colaboración Abierta**: Fomentamos las contribuciones de la comunidad para añadir nuevos bancos y funcionalidades.

## Configuración del Entorno de Desarrollo Local

Para empezar a contribuir, sigue estos pasos:

1.  **Clona el repositorio**:

    ```bash
    git clone https://github.com/tu_usuario/fintself.git
    cd fintself
    ```

2.  **Asegura los prerrequisitos**: Necesitas tener `Python >= 3.9` y `uv` instalados.

3.  **Instala las dependencias**: El `Makefile` se encarga de todo. Este comando creará un entorno virtual, instalará las dependencias de producción y desarrollo, y activará el hook de pre-commit.

    ```bash
    make install
    ```

    Si ya tenías el entorno creado, puedes instalar solo el hook con:

    ```bash
    make install-hooks
    ```

4.  **Configura tus credenciales (Opcional)**: Para ejecutar scrapers localmente, crea un archivo `.env` en la raíz del proyecto. **Este archivo está ignorado por Git y nunca debe ser subido**.

    ```
    # .env
    CL_SANTANDER_USER="tu_usuario_santander"
    CL_SANTANDER_PASSWORD="tu_clave_secreta"
    ```

5.  **Verifica la instalación**: Ejecuta el formateador de código y las pruebas para asegurarte de que todo está configurado correctamente.
    ```bash
    make format
    make test
    ```

## Estructura del Proyecto

El proyecto está organizado para separar claramente las responsabilidades, facilitar la navegación y la extensibilidad.

```
fintself/
├── .github/
│   └── workflows/
│       ├── ci.yml         # Integración continua (tests, linting)
│       └── release.yml    # Publicación automática en PyPI
├── debug_output/          # (Ignorado por Git) Salidas de depuración (HTML, screenshots)
├── fintself/
│   ├── __init__.py        # Expone la factory `get_scraper` y modelos principales
│   ├── cli.py             # Lógica de la CLI con Typer
│   ├── core/
│   │   ├── __init__.py
│   │   ├── exceptions.py  # Excepciones personalizadas (LoginError, etc.)
│   │   └── models.py      # Modelos Pydantic (MovementModel)
│   ├── scrapers/
│   │   ├── __init__.py    # Lógica de la factory `get_scraper`
│   │   ├── base.py        # Clase abstracta BaseScraper
│   │   └── cl/
│   │       ├── __init__.py # Expone los scrapers de Chile
│   │       └── santander.py
│   │   └── pe/
│   │       ├── __init__.py
│   │       └── bcp.py
│   └── utils/
│       ├── __init__.py
│       ├── logging.py     # Configuración de Loguru
│       └── output.py      # Funciones para exportar a JSON, CSV, XLSX
├── tests/
│   ├── fixtures/          # Archivos HTML mockeados para las pruebas
│   │   └── cl/
│   │       └── santander/
│   │           ├── login_page.html
│   │           └── movements_page.html
│   └── scrapers/
│       └── cl/
│           └── test_santander.py # Pruebas para el scraper de Santander
├── tutorials/
│   ├── 01_basic_usage.ipynb          # Tutorial interactivo para Jupyter.
│   ├── run_all_scrapers_visible.py # Script para ejecutar todos los scrapers en modo visible.
│   └── debug_scrapers.py             # Script para depurar un scraper específico.
├── .gitignore
├── CONTRIBUTING.md        # Esta guía
├── Makefile               # Comandos útiles para el desarrollo
├── pyproject.toml         # Definición del proyecto y dependencias
└── README.md
```

### Principios de Arquitectura

- `fintself/core`: Contiene la lógica central y agnóstica a cualquier banco. Aquí viven las `exceptions.py` personalizadas y los `models.py` de Pydantic. No debe haber código específico de un scraper aquí.
- `fintself/scrapers`: El corazón del proyecto. Contiene la `base.py` con la clase abstracta de la que todos los scrapers heredan. La lógica de cada banco está anidada por país (`cl/`, `pe/`, etc.).
- `fintself/utils`: Utilidades transversales como la configuración del `logging.py` y las funciones de `output.py` para exportar datos.
- `fintself/cli.py`: Define toda la interfaz de línea de comandos. Es el punto de entrada para los usuarios que usan `fintself` desde la terminal.
- `tests/`: Contiene todas las pruebas. Su estructura de directorios refleja la de `fintself/` para mantener el orden.
- `debug_output/`: Directorio (ignorado por Git) donde los scrapers guardan capturas de pantalla y HTML cuando se ejecutan en modo de depuración.

### Flujo de Desarrollo (Gitflow)

Usamos un flujo de trabajo basado en Gitflow:

- `main`: Contiene el código de producción (releases). Solo se fusiona desde `develop` para crear una nueva versión.
- `develop`: Es la rama principal de desarrollo. Todas las nuevas funcionalidades se fusionan aquí.
- `feature/<nombre-feature>`: Las nuevas funcionalidades o scrapers se desarrollan en estas ramas, que se crean a partir de `develop`.

### Cómo Añadir un Nuevo Scraper

Este es el flujo de trabajo para agregar soporte para un nuevo banco:

1.  **Crear una Rama**: Desde `develop`, crea una nueva rama: `git checkout -b feature/scraper-banco-xyz develop`.

2.  **Crear el Archivo del Scraper**: Crea un nuevo archivo Python en `fintself/scrapers/codigo_iso_pais/nombre_banco.py`.

    - _Ejemplo_: `fintself/scrapers/cl/banco_estado.py`.

3.  **Implementar la Clase Scraper**:

    - Hereda de `fintself.scrapers.base.BaseScraper`.
    - Implementa los métodos abstractos `login()` y `scrape_data()`.
    - La clase debe recibir las credenciales en su `__init__`.
    - El método `scrape_data()` debe devolver una lista de `MovementModel`.

4.  **Guardar HTML para Pruebas (Mocks)**:

    - Mientras desarrollas, guarda el contenido HTML de las páginas clave (login, movimientos, etc.).
    - Crea una carpeta en `tests/fixtures/codigo_iso_pais/nombre_banco/`.
    - Guarda los archivos HTML ahí. _Ejemplo_: `login.html`, `movements_page_1.html`.
    - **Importante**: Edita los archivos HTML para eliminar cualquier información personal o sensible.

5.  **Escribir las Pruebas**:

    - Crea un archivo de prueba en `tests/scrapers/codigo_iso_pais/test_nombre_banco.py`.
    - Usa `pytest` y `pytest-mock`.
    - En tus pruebas, "mockea" las llamadas a Playwright para que en lugar de navegar a una URL, lean el contenido de tus archivos HTML locales.

6.  **Exponer el Scraper**:

    - En `fintself/scrapers/codigo_iso_pais/__init__.py`, importa tu nueva clase para que sea accesible.
    - Añade tu scraper al diccionario en `fintself/scrapers/__init__.py` para que la factory `get_scraper` pueda encontrarlo.

7.  **Verificar el Código**:

    - Formatea tu código: `make format`.
    - Ejecuta el lint que corre antes de cada commit: `make pre-commit`.
    - Ejecuta las pruebas: `make test`.

8.  **Crear un Pull Request**: Envía un PR de tu rama `feature/...` a `develop`.

### Guía para Pull Requests (PRs)

Para que tu contribución sea revisada y fusionada eficientemente:

- **Vincula a un Issue**: Si tu PR resuelve un issue existente, asegúrate de referenciarlo en la descripción (ej: `Closes #123`).
- **Título Claro**: Usa un título descriptivo. Recomendamos seguir el estándar de [Conventional Commits](https://www.conventionalcommits.org/) (ej: `feat(scrapers): add support for Banco Estado Chile`).
- **Descripción Detallada**: Explica qué hace tu PR y por qué. Si hay cambios visuales, incluye capturas de pantalla.
- **PRs Pequeños y Enfocados**: Evita PRs que hagan muchas cosas a la vez. Es mejor enviar varios PRs pequeños que uno grande.
- **Verifica tus Cambios**: Asegúrate de que `make test` y `make format` se ejecutan sin errores antes de enviar el PR.

### Reporte de Bugs y Sugerencias

Usa la sección de **Issues** de GitHub para reportar bugs o proponer nuevas funcionalidades. Por favor, sé lo más detallado posible, incluyendo:

- Versión del paquete.
- Pasos para reproducir el error.
- Comportamiento esperado vs. comportamiento actual.
- Logs o capturas de pantalla relevantes.

### Estándares de Código

- **Lenguaje**: El código, los docstrings y los comentarios deben estar en **inglés**. El README, el CONTRIBUTING y los tutoriales debe estar en **español**.
- **Formato**: Usamos `Ruff` con el perfil de `Black`. Ejecuta `make format` antes de hacer commit.
- **Docstrings**: Usa el formato [Google Style](https://github.com/google/styleguide/blob/gh-pages/pyguide.md#38-comments-and-docstrings).
- **Logging**: Usa `Loguru` para un logging estructurado y útil. La configuración se centraliza en `fintself/utils/logging.py`.
- **Excepciones**: Lanza excepciones específicas que hereden de `fintself.core.exceptions.FintselfException`. Ejemplos: `LoginError`, `DataExtractionError`.

### Manejo de Dependencias (uv)

Todas las dependencias del proyecto se gestionan con `uv`. No modifiques el archivo `pyproject.toml` manualmente para añadir paquetes.

- **Para añadir una dependencia de producción** (necesaria para que el paquete funcione para el usuario final, como `pandas` o `typer`):

  ```bash
  uv add <nombre-del-paquete>
  ```

- **Para añadir una dependencia de desarrollo** (necesaria solo para desarrollar, como `pytest` o `ruff`):
  ```bash
  uv add --dev <nombre-del-paquete>
  ```
  _Ejemplo real del proyecto_: `uv add --dev pytest pytest-mock ruff`

### Pruebas

Las pruebas son **obligatorias** para cada scraper.

- **No deben hacer peticiones de red**.
- **No deben usar credenciales reales**.
- Deben cargar el HTML desde `tests/fixtures` y simular la interacción del scraper con ese contenido estático.

### Depuración (Debugging)

Para facilitar el desarrollo y la corrección de errores en los scrapers, puedes usar el modo de depuración.

- **Activación desde la CLI**: Usa el flag `--debug`.
  ```bash
  fintself scrape cl_santander --output-file out.xlsx --debug
  ```
- **Activación desde código Python**: Pasa `debug_mode=True` a la factory o al constructor del scraper.
  ```python
  scraper = get_scraper("cl_santander", debug_mode=True)
  ```
- **¿Qué hace?**: Cuando está activo, el scraper guardará capturas de pantalla (`.png`) y el contenido HTML (`.html`) de los pasos clave del proceso en el directorio `debug_output/<bank_id>/`. Este directorio está ignorado por Git. Estos archivos son increíblemente útiles para entender por qué un scraper falla.

### Interfaz de Línea de Comandos (CLI)

La CLI se construye con `Typer` y sigue las mejores prácticas de seguridad.

- **Comandos**: `fintself list` y `fintself scrape <bank_id>`.
- **Credenciales**:
  1.  El programa busca variables de entorno (ej: `CL_SANTANDER_USER`).
  2.  Si no las encuentra, pide los datos de forma interactiva usando `getpass` para la contraseña.
- **Salida**: Requiere un archivo de salida con `--output-file`. Por defecto, el formato es XLSX, pero se puede controlar con `--output-format`.

### Manejo de Versiones y Proceso de Release

El proyecto utiliza un **proceso de release totalmente automatizado** basado en **Commits Convencionales**.

Cuando se fusiona un Pull Request a la rama `main`, el workflow de `Release` se activa y realiza las siguientes acciones de forma automática:
1.  Analiza los mensajes de los commits desde el último release.
2.  Determina el tipo de cambio (`fix`, `feat`, `BREAKING CHANGE`).
3.  Calcula el nuevo número de versión semántica (`MAYOR.MENOR.PARCHE`).
4.  Actualiza el archivo `pyproject.toml` con la nueva versión.
5.  Crea y empuja un tag de Git con la nueva versión (ej: `v1.2.3`).
6.  Genera notas de release (Changelog) automáticas.
7.  Publica el paquete en PyPI.
8.  Crea un "Release" en la página de GitHub.

Gracias a esta automatización, los mantenedores no necesitan realizar ningún paso manual para lanzar una nueva versión. Solo es necesario asegurarse de que los Pull Requests se fusionen con mensajes de commit que sigan el estándar de Commits Convencionales.

**Nota para mantenedores**: Para que la publicación en PyPI funcione, es necesario configurar un secreto en el repositorio de GitHub llamado `PYPI_API_TOKEN` con un token de API válido de PyPI.
