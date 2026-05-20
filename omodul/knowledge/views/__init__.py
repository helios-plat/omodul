"""omodul.knowledge.views — View CRUD, applier, and preset loader."""
from omodul.knowledge.views.applier import apply_view
from omodul.knowledge.views.crud import (
    create_view,
    delete_view,
    get_default_view,
    get_view,
    list_views,
    set_default,
    update_view,
)
from omodul.knowledge.views.preset_loader import install_builtin_views

__all__ = [
    "create_view",
    "get_view",
    "list_views",
    "update_view",
    "delete_view",
    "set_default",
    "get_default_view",
    "apply_view",
    "install_builtin_views",
]
