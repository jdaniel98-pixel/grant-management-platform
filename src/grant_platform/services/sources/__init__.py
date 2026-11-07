"""Adapters for monitored grant sources. One module per source; each exposes SOURCE (its grant_source row) and an
adapter class with discover(fetcher), which yields plain grant fields and never decides verification."""
