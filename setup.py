#!/usr/bin/env python
"""Setup script for SINCOR2"""

from setuptools import setup, find_packages

setup(
    name="sincor2",
    version="1.0.0",
    packages=find_packages(exclude=["tests", "examples", "docs"]),
    install_requires=[
        "Flask>=3.0.0",
        "Flask-JWT-Extended>=4.5.0",
        "Pydantic>=2.4.0",
        "python-dotenv>=1.0.0",
        "gunicorn>=21.0.0",
    ],
    python_requires=">=3.9",
)
