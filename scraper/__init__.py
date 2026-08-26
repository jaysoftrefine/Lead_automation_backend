"""Scraper package initialization."""

from scraper.base import BaseScraper
from scraper.jobspy_scraper import JobSpyScraper

__all__ = ["BaseScraper", "JobSpyScraper"]
