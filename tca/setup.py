from setuptools import find_packages, setup

setup(
    name="tca",
    version="0.1.0",
    packages=find_packages(exclude=["tests*", "dags*", "frontend*"]),
    python_requires=">=3.11",
    install_requires=[
        "fastapi>=0.111.0",
        "uvicorn[standard]>=0.29.0",
        "sqlalchemy>=2.0.0",
        "psycopg2-binary>=2.9.0",
        "python-multipart>=0.0.9",
        "pydantic>=2.0.0",
        "httpx>=0.27.0",
        "python-jose[cryptography]>=3.3.0",
        "bcrypt>=4.0.0",
        "dlt[postgres]>=1.5.0",
        "faker>=25.0.0",
        "numpy>=1.26.0",
        "pandas>=2.2.0",
        "scipy>=1.13.0",
        "dbt-postgres>=1.8.0,<1.9.0",
        "redis[hiredis]>=5.0.0",
        "openpyxl>=3.1.0",
        "python-dotenv>=1.0.0",
    ],
)
