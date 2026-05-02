import os

from dotenv import load_dotenv

from fintself import get_scraper
from fintself.utils.logging import logger
from fintself.utils.output import save_to_xlsx

# Load environment variables from a .env file.
# Make sure to have your .env file in the project root.
load_dotenv()

# List of all banks you want to process
BANKS_TO_SCRAPE = ["cl_estado", "cl_santander", "cl_banco_chile", "cl_cencosud", "cl_bice"]


def main():
    """
    Main function that runs scrapers for all banks.

    To run in visible mode (seeing the browser), make sure to have the
    following line in your .env file:
    SCRAPER_HEADLESS_MODE=false
    """
    for bank_id in BANKS_TO_SCRAPE:
        logger.info(f"--- Starting process for: {bank_id} ---")

        # Build environment variable names for credentials
        user_env_var = f"{bank_id.upper()}_USER"
        password_env_var = f"{bank_id.upper()}_PASSWORD"

        # Get credentials from environment
        user = os.getenv(user_env_var)
        password = os.getenv(password_env_var)

        if not user or not password:
            logger.warning(
                f"Credentials for {bank_id} not found in .env file. Skipping..."
            )
            continue

        try:
            # Get the scraper instance. Configuration (headless, debug)
            # will be taken from .env file
            scraper = get_scraper(bank_id, headless=False)

            # Execute the scraper
            movements = scraper.scrape(user=user, password=password)

            if movements:
                output_filename = f"outputs/{bank_id}_movements.xlsx"
                save_to_xlsx(movements, output_filename)
                logger.success(
                    f"Found and saved {len(movements)} movements for {bank_id} in '{output_filename}'."
                )
            else:
                logger.info(f"No movements found for {bank_id}.")

        except Exception as e:
            logger.error(
                f"An error occurred while processing {bank_id}: {e}", exc_info=True
            )


if __name__ == "__main__":
    main()
