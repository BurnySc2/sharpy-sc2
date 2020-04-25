from typing import TYPE_CHECKING

from sc2.client import Client

if TYPE_CHECKING:
    from sharpy.knowledges import Knowledge, KnowledgeBot
    from sharpy.managers import *


class Component:
    """
    Common component for all sharpy objects that contains shortcuts to managers

    Attributes:
    """

    # Shortcuts to various managers
    knowledge: "Knowledge"
    ai: "KnowledgeBot"
    client: Client
    cache: "UnitCacheManager"
    unit_values: "UnitValue"
    pather: "PathingManager"
    combat: "GroupCombatManager"
    roles: "UnitRoleManager"
    zone_manager: "ZoneManager"
    cd_manager: "CooldownManager"

    def __init__(self) -> None:
        self._debug: bool = False

    @property
    def debug(self):
        return self._debug and self.knowledge.debug

    async def start(self, knowledge: "Knowledge"):
        self.knowledge = knowledge
        self._debug = self.knowledge.get_boolean_setting(f"debug.{type(self).__name__}")
        self.ai = knowledge.ai
        self.cache = knowledge.unit_cache
        self.unit_values = knowledge.unit_values
        self.client = self.ai._client
        self.pather = self.knowledge.pathing_manager
        self.combat = self.knowledge.combat_manager
        self.roles = self.knowledge.roles
        self.zone_manager = self.knowledge.zone_manager
        self.cd_manager = knowledge.cooldown_manager

    def print(self, msg: str, stats: bool = True):
        self.knowledge.print(msg, type(self).__name__, stats)