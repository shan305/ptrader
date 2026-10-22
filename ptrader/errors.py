class PTraderError(Exception):
    """Base for errors that map to an HTTP response."""

    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail

    def to_dict(self) -> dict:
        return {"detail": self.detail}


class NotFound(PTraderError):
    status_code = 404


class Conflict(PTraderError):
    status_code = 409


class InvalidRequest(PTraderError):
    status_code = 422


class PricingError(PTraderError):
    """No quote or conversion rate is available for a calculation."""

    status_code = 409


class OrderRejected(PTraderError):
    """The order was recorded with status 'rejected'."""

    status_code = 422

    def __init__(self, detail: str, order_id: int):
        super().__init__(detail)
        self.order_id = order_id

    def to_dict(self) -> dict:
        return {"detail": self.detail, "order_id": self.order_id}


class Rejection(Exception):
    """A business rule refused an order. Raised inside the order lifecycle only;
    the lifecycle records the rejected order and turns this into OrderRejected."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
