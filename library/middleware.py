class PasswordChangeRequiredMiddleware:
    allowed_paths = {"/password/change/", "/logout/", "/health/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.session.get("must_change_password")
            and request.path not in self.allowed_paths
        ):
            from django.shortcuts import redirect

            return redirect("first_password_change")
        return self.get_response(request)

