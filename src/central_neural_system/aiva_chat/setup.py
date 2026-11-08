import os
from pathlib import Path
from setuptools import find_packages, setup

package_name = "aiva_chat"
soul_file = Path(__file__).resolve().parents[3] / "persona" / "SOUL.md"
soul_source = os.path.relpath(soul_file, Path.cwd())

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/persona", [soul_source]),
    ],
    install_requires=["setuptools", "httpx", "psycopg[binary]>=3.2,<4"],
    zip_safe=True,
    maintainer="OppaAI",
    maintainer_email="oppa.ai.org@gmail.com",
    description="AIVA LLM chat and simple conversation memory",
    license="GPL-3.0-only",
    entry_points={"console_scripts": ["aiva_chat = aiva_chat.node:main"]},
)
