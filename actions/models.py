from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, declarative_base

# The declarative base is a factory for creating model classes.
Base = declarative_base()

class City(Base):
    __tablename__ = 'cities'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)

    # This creates a one-to-many relationship: one city can have many airports.
    airports = relationship("Airport", back_populates="city")
    __table_args__ = (Index('idx_cities_name_lower', 'name', postgresql_using='btree', postgresql_ops={'name': 'text_pattern_ops'}),)

class UserPreference(Base):
    __tablename__ = 'user_preferences'
    user_id = Column(String(255), primary_key=True)
    preference_key = Column(String(50), primary_key=True)
    preference_value = Column(String(255), nullable=False)

class Airport(Base):
    __tablename__ = 'airports'
    id = Column(Integer, primary_key=True)
    city_id = Column(Integer, ForeignKey('cities.id', ondelete='CASCADE'), nullable=False, index=True)
    airport_name = Column(String(100), nullable=False)
    iata_code = Column(String(3), unique=True, nullable=False)

    # This creates the other side of the relationship.
    city = relationship("City", back_populates="airports")