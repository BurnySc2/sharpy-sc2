from typing import Optional, List, Dict, Tuple

from sc2.constants import IS_COLLECTING, ALL_GAS
from sharpy.managers import UnitRoleManager
from sharpy.managers.unit_value import buildings_5x5
from sharpy.plans.acts import ActBase
from sc2.ids.buff_id import BuffId
from sc2.units import Units

from sharpy.managers.roles import UnitTask
from sc2 import UnitTypeId, Race, AbilityId
from sc2.unit import Unit, UnitOrder

from sharpy.knowledges import Knowledge
from sharpy.general.zone import Zone

MAX_WORKERS_PER_GAS = 3
ZONE_EVACUATION_POWER_THRESHOLD = -5
BAD_ZONE_POWER_THRESHOLD = -2


class WorkStatus:
    def __init__(self, unit: Unit, available: int, force_exit: bool = False) -> None:
        self.force_exit = force_exit
        self.unit = unit
        self.available = available


class PlanDistributeWorkersV2(ActBase):
    """Handles idle workers and worker distribution."""

    def __init__(self, min_gas: Optional[int] = None, max_gas: Optional[int] = None):
        super().__init__()
        assert min_gas is None or isinstance(min_gas, int)
        assert max_gas is None or isinstance(max_gas, int)

        self.min_gas = min_gas
        self.max_gas = max_gas

        self.active_gas_workers = 0
        self.roles: UnitRoleManager = None
        self.force_work = False
        # workplace tag to tags of workers there
        self.worker_dict: Dict[int, List[int]] = dict()
        self.work_queue: List[WorkStatus] = []

    async def start(self, knowledge: Knowledge):
        await super().start(knowledge)
        self.roles = knowledge.roles

    async def execute(self) -> bool:
        self.worker_dict.clear()
        self.calculate_workers()
        self.generate_worker_queue()

        for worker in (
            self.roles.all_from_task(UnitTask.Idle).of_type(self.unit_values.worker_types)
            + self.roles.all_from_task(UnitTask.Gathering).idle
        ):  # type: Unit
            # Re-assign idle workers
            await self.set_work(worker)

        # Balance workers in bases that have to many
        work_status: Optional[WorkStatus] = None
        for status in self.work_queue[::-1]:
            if status.available < 0:
                work_status = status
                break

        if work_status:
            tags = self.worker_dict.get(work_status.unit.tag, [])
            if tags:
                assign_workers = self.cache.by_tags(tags)
                if assign_workers:
                    assign_worker = assign_workers.furthest_to(work_status.unit)
                    await self.set_work(assign_worker, work_status)

        # TODO: Force Assign workers to gas as needed. Setting perhaps for this?
        return True

    @property
    def active_gas_buildings(self) -> Units:
        """All gas buildings that are ready."""
        # todo: filter out gas buildings that do not have a nexus nearby (it has been destroyed)?
        return self.ai.gas_buildings.ready

    @property
    def safe_non_full_gas_buildings(self) -> Units:
        """All gas buildings that are on a safe zone and could use more workers."""
        result = Units([], self.ai)

        for zone in self.knowledge.our_zones:  # type: Zone
            if zone.is_under_attack:
                continue

            filtered = filter(lambda g: g.surplus_harvesters < 0, zone.gas_buildings)
            result.extend(filtered)

        return result

    @property
    def gas_workers_target(self) -> int:
        """Target count for workers harvesting gas."""
        worker_count = self.knowledge.roles.free_workers.amount
        max_workers_at_gas = self.active_gas_buildings.amount * MAX_WORKERS_PER_GAS

        estimate = round((worker_count - 8) / 2)
        if self.min_gas is not None:
            estimate = max(estimate, self.min_gas)

        if self.max_gas is not None:
            estimate = min(estimate, self.max_gas)

        return min(max_workers_at_gas, estimate)

    def add_worker(self, worker: Unit, target: Unit):
        worker_list = self.worker_dict.get(target.tag, [])
        if not worker_list:
            self.worker_dict[target.tag] = worker_list
        worker_list.append(worker.tag)

    def calculate_workers(self):
        if not self.ai.townhalls:
            # can't mine anything
            return

        for worker in self.ai.workers:
            # worker.is_gathering
            if worker.orders:
                order: UnitOrder = worker.orders[-1]

                if order.ability.id in IS_COLLECTING and isinstance(order.target, int):
                    obj = self.cache.by_tag(order.target)
                    if obj:
                        if obj.is_mineral_field:
                            townhall = self.ai.townhalls.closest_to(obj)
                            self.add_worker(worker, townhall)
                        elif obj.type_id in ALL_GAS:
                            self.add_worker(worker, obj)

                        if obj.type_id in buildings_5x5:
                            if worker.is_carrying_minerals:
                                self.add_worker(worker, obj)
                            elif worker.is_carrying_vespene:
                                if self.ai.gas_buildings:
                                    gas_building = self.ai.gas_buildings.closest_to(worker)
                                    self.add_worker(worker, gas_building)

                        # self.print(
                        #     f"worker {worker.tag} is {order.ability.id.name} to {order.target} {obj.type_id.name}"
                        # )

    def generate_worker_queue(self):
        self.work_queue.clear()
        self.active_gas_workers = 0

        for building in self.ai.gas_buildings + self.ai.townhalls:
            if building.ideal_harvesters == 0:
                # Ignore empty buildings
                continue

            current_workers = len(self.worker_dict.get(building.tag, []))
            zone = self.zone_manager.zone_manager.zone_for_unit(building)
            if zone and zone.needs_evacuation:
                # Exit workers from the zone
                self.work_queue.append(WorkStatus(building, -current_workers * 10, True))
            elif building.has_vespene:
                self.active_gas_workers += 1
                # One worker should be inside the gas
                harvesters = min(building.assigned_harvesters, current_workers + 1)
                self.work_queue.append(WorkStatus(building, building.ideal_harvesters - harvesters))
            else:
                self.zone_manager.zone_for_unit(building)
                self.work_queue.append(WorkStatus(building, building.ideal_harvesters - current_workers))

        if self.active_gas_workers > self.gas_workers_target:

            def sort_method(tpl: WorkStatus):
                if tpl.unit.type_id in buildings_5x5:
                    return tpl.available
                return tpl.available * 100

        else:

            def sort_method(tpl: WorkStatus):
                if tpl.unit.type_id in buildings_5x5:
                    return tpl.available * 100
                return tpl.available

        self.work_queue.sort(key=sort_method)

        # for queue in self.work_queue:
        #     self.print(f"Queue: {queue.unit.type_id.name} {queue.unit.tag}: {queue.available}")

    async def set_work(self, worker, last_work_status: Optional[WorkStatus] = None):
        if last_work_status:
            typename = last_work_status.unit.type_id.name
            self.print(
                f"Worker {worker.tag} needs better work! {typename} {last_work_status.unit.tag}: {last_work_status.available}"
            )
        else:
            self.print(f"Worker {worker.tag} needs new work!")
        new_work = self.get_new_work(worker, last_work_status)

        if new_work is None:
            self.print(f"No work to assign worker {worker.tag} to.")
            return True

        if new_work.type_id in buildings_5x5:
            for zone in self.zone_manager.expansion_zones:  # type: Zone
                if zone.center_location.distance_to(new_work.position) < 1:
                    new_work = zone.check_best_mineral_field()
                    break

        self.print(f"New work found, gathering {new_work.type_id} {new_work.tag}!")
        self.assign_to_work(worker, new_work)
        return True  # Always non-blocking

    def get_new_work(self, worker: Unit, last_work_status: Optional[WorkStatus] = None) -> Optional[Unit]:
        current_work: WorkStatus = None

        for status in self.work_queue:
            if status.unit.has_vespene:
                if status.available > 0:
                    current_work = status
                    break
            else:
                if status.available > 0:
                    current_work = status
                    break

                if current_work is None:
                    current_work = status
                else:
                    if current_work.available == status.available and current_work.unit.distance_to(
                        worker
                    ) > status.unit.distance_to(worker):
                        current_work = status

        if current_work:
            if last_work_status:
                if last_work_status.unit.tag == current_work.unit.tag:
                    # Don't move workers from one job to same job
                    return None

                if current_work.available < 0 and not last_work_status.unit.has_vespene:
                    # Don't move workers from overcrowded mineral mining to another overcrowded mineral mining
                    return None

            current_work.available -= 1
            return current_work.unit
        return None

    def assign_to_work(self, worker: Unit, work: Unit):
        if worker.has_buff(BuffId.ORACLESTASISTRAPTARGET):
            return  # Worker is in stasis and cannot move

        self.roles.set_task(UnitTask.Gathering, worker)
        townhalls = self.ai.townhalls.ready

        self.roles.set_task(UnitTask.Gathering, worker)

        if worker.is_carrying_resource and townhalls:
            closest = townhalls.closest_to(worker)
            self.do(worker(AbilityId.SMART, closest))
            self.do(worker.gather(work, queue=True))
        else:
            self.do(worker.gather(work))
