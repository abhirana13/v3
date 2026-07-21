"""Dashboards — grids of widgets that compose EXISTING charts.

A widget never runs SQL or touches Redshift: it references a source chart and
reads that chart's already-backpopulated DuckDB cache through the existing
serving layer. The whole feature is metadata (this package's models/CRUD/API)
plus serving composition (serving.py) on top of app.serving.serve_data.
"""
