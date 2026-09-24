"""The browser editor's HTTP server. See server/http.py."""


class EditorError(Exception):
    """A request that can't be carried out, with the HTTP status that says why.

    Lives here rather than beside the controller that raises it so the server
    needs nothing from the UI package, which imports the server.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
