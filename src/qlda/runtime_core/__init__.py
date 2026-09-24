"""QLDA V7.6 packaged production runtime core."""

# Install the Contractor Data Hub completeness guard as soon as the packaged
# runtime_core namespace is imported.  This keeps Streamlit and background sync
# workers on the same row-ingestion semantics without duplicating boot logic.
from qlda.runtime_core.contractor_data_complete_rows import install_contractor_data_complete_rows

install_contractor_data_complete_rows()
