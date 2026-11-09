from django.urls import include, path

from catalog.http import CategoriesView, ProductActiveView, ProductCategoryView, ProductCreateView, ProductDetailView
from identity.http import OperatorLoginView, csrf_token, session_logout, session_status
from inventory.http import InitialReceiptView, MovementsView, StockView

urlpatterns = [
    path("api/v1/", include("system.urls")),
    path("api/v1/operator/auth/csrf", csrf_token, name="operator-csrf"),
    path("api/v1/operator/auth/login", OperatorLoginView.as_view(), name="operator-login"),
    path("api/v1/operator/auth/session", session_status, name="operator-session"),
    path("api/v1/operator/auth/logout", session_logout, name="operator-logout"),
    path("api/v1/operator/catalog/products", ProductCreateView.as_view(), name="operator-products"),
    path("api/v1/operator/catalog/products/<str:product_id>", ProductDetailView.as_view(), name="operator-product"),
    path("api/v1/operator/catalog/categories", CategoriesView.as_view(), name="operator-categories"),
    path("api/v1/operator/catalog/products/<str:product_id>/category", ProductCategoryView.as_view(), name="operator-product-category"),
    path("api/v1/operator/catalog/products/<str:product_id>/active", ProductActiveView.as_view(), name="operator-product-active"),
    path("api/v1/operator/inventory/variants/<str:variant_id>/initial-receipt", InitialReceiptView.as_view(), name="operator-initial-receipt"),
    path("api/v1/operator/inventory/variants/<str:variant_id>/stock", StockView.as_view(), name="operator-stock"),
    path("api/v1/operator/inventory/variants/<str:variant_id>/movements", MovementsView.as_view(), name="operator-movements"),
]
