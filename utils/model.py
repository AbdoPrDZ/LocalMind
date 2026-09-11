
from sqlalchemy.orm import DeclarativeBase


class BaseModel(DeclarativeBase):
  __description__: str
