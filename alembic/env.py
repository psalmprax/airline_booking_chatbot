import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# --- Automation for local development ---
# Add the 'actions' directory to the Python path to find the models.
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..', 'actions')))

# Load environment variables from .env file located in the project root.
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
# --- End Automation ---

# Import your models' Base
from models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# my_important_option = config.get_main_option("my_important_option")
# ... etc.


# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = None
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
