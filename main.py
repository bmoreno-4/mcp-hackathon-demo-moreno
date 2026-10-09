"""Root entry point for local development.

Run locally:
    uv run python main.py

Or via the installed script:
    uv run lrl-reservoirs
"""

from lrl_reservoirs.app import main

if __name__ == "__main__":
    main()
