from django.http import Http404
from django.shortcuts import render


class PaginaNoEncontradaMiddleware:
    """Mantiene el 404 visual también para recursos inexistentes en desarrollo."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, Http404):
            return render(request, '404.html', status=404)
        return None
