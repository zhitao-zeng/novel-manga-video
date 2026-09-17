"""Process-local input cache shared by history, inventory and repair statistics."""
from novel_manga.dashboard.files import DashboardFiles

files = DashboardFiles()


def scan_book(novel):
    return files.scan(novel)
