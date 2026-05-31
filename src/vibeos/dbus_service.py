import asyncio
import json
from dataclasses import asdict

from .broker import CapabilityBroker
from .models import CommandRequest


def run_dbus_service(broker: CapabilityBroker) -> int:
    try:
        from dbus_next.aio import MessageBus
        from dbus_next.service import ServiceInterface, method
    except ImportError:
        print("dbus-next is required for D-Bus service mode")
        return 2

    class AgentInterface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.vibeos.Agent")

        @method()
        def Command(self, text: "s") -> "s":
            result = broker.handle(CommandRequest(text))
            return json.dumps(asdict(result), ensure_ascii=False)

        @method()
        def AppsList(self) -> "s":
            return json.dumps([asdict(app) for app in broker.apps.list_apps()], ensure_ascii=False)

        @method()
        def WindowsList(self) -> "s":
            return json.dumps([asdict(window) for window in broker.windows.list_windows()], ensure_ascii=False)

        @method()
        def ApproveReview(self, review_id: "s") -> "s":
            result = broker.handle(CommandRequest("", review_id=review_id, approve=True))
            return json.dumps(asdict(result), ensure_ascii=False)

        @method()
        def RejectReview(self, review_id: "s") -> "s":
            result = broker.reject_review(review_id)
            return json.dumps(asdict(result), ensure_ascii=False)

        @method()
        def Capabilities(self) -> "s":
            return json.dumps(broker.capabilities(), ensure_ascii=False)

        @method()
        def PendingReviews(self) -> "s":
            return json.dumps(broker.pending_reviews(), ensure_ascii=False)

    async def serve() -> None:
        bus = await MessageBus().connect()
        await bus.request_name("org.vibeos.Agent")
        bus.export("/org/vibeos/Agent", AgentInterface())
        print("vibed D-Bus service ready: org.vibeos.Agent")
        await asyncio.Event().wait()

    asyncio.run(serve())
    return 0
