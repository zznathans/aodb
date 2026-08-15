"""Item/nano-program storage, backed by Redis so the dataset (~65MB/125k
items, ~10.5k of them nano programs) is held once, shared across all pods,
rather than each pod loading its own full in-memory copy.

Key schema (all keys use decode_responses=True string values):
  item:{id}                    hash: name, ql, icon, description?
  items:by_name                zset: member "{name_lower}\\0{id}", score 0
  items:by_name:ql:{ql}        zset: same members, items with that exact ql

  nano:{id}                    hash: name, ql, icon, description?, school?,
                                strain?, nanocost?, ncu?, crystal_id?,
                                duration?, profession?, requirements (JSON)
  nanos:by_name                zset: member "{name_lower}\\0{id}", score 0

Name search is prefix-only (ZRANGEBYLEX on the *_by_name zsets), not the
substring match the old in-memory implementation did - substring matching
isn't expressible as an efficient range query over plain Redis data
structures, and pulling the whole dataset back to filter in Python for
every request would defeat the point of not holding it in memory.

Items only ever filter by ql, so a per-ql zset gives an efficient combined
(prefix + ql) index. Nanos additionally filter by school/profession, which
aren't practical to pre-index for every combination; when either is
supplied, all name-prefix matches are pulled back and filtered in Python
before sorting/paging. That's fine at nano scale (~10.5k rows) but would
not scale to the full item set - hence items don't do this.
"""

import json
from dataclasses import dataclass, field

from .redis_client import get_redis

_NUL = "\x00"


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    name_lower: str
    ql: int
    icon: int
    description: str | None


def make_item(id: int, name: str, ql: int = 0, icon: int = 0, description: str | None = None) -> Item:
    return Item(id=id, name=name, name_lower=name.lower(), ql=ql, icon=icon, description=description)


@dataclass(frozen=True)
class Requirement:
    attribute: str
    operator: str
    value: str


@dataclass(frozen=True)
class NanoProgram:
    id: int
    name: str
    name_lower: str
    ql: int
    icon: int
    description: str | None
    school: str | None
    strain: int | None
    nanocost: int | None
    ncu: int | None
    crystal_id: int | None
    duration: int | None
    profession: int | None
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)


def make_nano(
    id: int,
    name: str,
    ql: int = 0,
    icon: int = 0,
    description: str | None = None,
    school: str | None = None,
    strain: int | None = None,
    nanocost: int | None = None,
    ncu: int | None = None,
    crystal_id: int | None = None,
    duration: int | None = None,
    profession: int | None = None,
    requirements: tuple[Requirement, ...] = (),
) -> NanoProgram:
    return NanoProgram(
        id=id,
        name=name,
        name_lower=name.lower(),
        ql=ql,
        icon=icon,
        description=description,
        school=school,
        strain=strain,
        nanocost=nanocost,
        ncu=ncu,
        crystal_id=crystal_id,
        duration=duration,
        profession=profession,
        requirements=requirements,
    )


def _member(name_lower: str, id_: int) -> str:
    return f"{name_lower}{_NUL}{id_}"


def _member_id(member: str) -> int:
    return int(member.rsplit(_NUL, 1)[1])


def _lex_range(prefix: str) -> tuple[str, str]:
    return f"[{prefix}", f"({prefix}\xff"


_BATCH_SIZE = 1000


class ItemStore:
    def __init__(self, key_prefix: str = "items") -> None:
        self._prefix = key_prefix

    def _item_key(self, id_: int) -> str:
        return f"item:{id_}"

    def _by_name_key(self) -> str:
        return f"{self._prefix}:by_name"

    def _by_name_ql_key(self, ql: int) -> str:
        return f"{self._prefix}:by_name:ql:{ql}"

    async def load(self, items: list[Item]) -> None:
        client = get_redis()
        for start in range(0, len(items), _BATCH_SIZE):
            batch = items[start : start + _BATCH_SIZE]
            pipe = client.pipeline(transaction=False)
            for item in batch:
                mapping: dict[str, str | int] = {"name": item.name, "ql": item.ql, "icon": item.icon}
                if item.description is not None:
                    mapping["description"] = item.description
                pipe.hset(self._item_key(item.id), mapping=mapping)

                member = _member(item.name_lower, item.id)
                pipe.zadd(self._by_name_key(), {member: 0})
                if item.ql:
                    pipe.zadd(self._by_name_ql_key(item.ql), {member: 0})
            await pipe.execute()

    def _hash_to_item(self, id_: int, h: dict[str, str]) -> Item:
        return make_item(
            id=id_,
            name=h["name"],
            ql=int(h["ql"]),
            icon=int(h["icon"]),
            description=h.get("description"),
        )

    async def count(self, query: str, ql: int) -> int:
        client = get_redis()
        key = self._by_name_ql_key(ql) if ql else self._by_name_key()
        lo, hi = _lex_range(query.lower())
        return await client.zlexcount(key, lo, hi)

    async def search(self, query: str, ql: int, limit: int, offset: int = 0) -> list[Item]:
        client = get_redis()
        key = self._by_name_ql_key(ql) if ql else self._by_name_key()
        lo, hi = _lex_range(query.lower())
        members = await client.zrangebylex(key, lo, hi, start=offset, num=limit)
        if not members:
            return []

        ids = [_member_id(m) for m in members]
        pipe = client.pipeline(transaction=False)
        for id_ in ids:
            pipe.hgetall(self._item_key(id_))
        hashes = await pipe.execute()
        return [self._hash_to_item(id_, h) for id_, h in zip(ids, hashes) if h]

    async def get(self, aoid: int) -> Item | None:
        client = get_redis()
        h = await client.hgetall(self._item_key(aoid))
        if not h:
            return None
        return self._hash_to_item(aoid, h)

    async def list_ids(self, limit: int, offset: int = 0) -> list[int]:
        """Cheap id-only enumeration (skips the per-item hash fetch
        search() does) - used by the sitemap generator, which only needs
        ids to build /browse/items/{id} URLs, not full item data."""
        client = get_redis()
        lo, hi = _lex_range("")
        members = await client.zrangebylex(self._by_name_key(), lo, hi, start=offset, num=limit)
        return [_member_id(m) for m in members]


def _requirements_to_json(requirements: tuple[Requirement, ...]) -> str:
    return json.dumps([r.__dict__ for r in requirements])


def _requirements_from_json(raw: str | None) -> tuple[Requirement, ...]:
    if not raw:
        return ()
    return tuple(Requirement(**r) for r in json.loads(raw))


class NanoStore:
    def __init__(self, key_prefix: str = "nanos") -> None:
        self._prefix = key_prefix

    def _nano_key(self, id_: int) -> str:
        return f"nano:{id_}"

    def _by_name_key(self) -> str:
        return f"{self._prefix}:by_name"

    def _school_counts_key(self) -> str:
        return f"{self._prefix}:school_counts"

    async def load(self, nanos: list[NanoProgram]) -> None:
        client = get_redis()

        for start in range(0, len(nanos), _BATCH_SIZE):
            batch = nanos[start : start + _BATCH_SIZE]
            pipe = client.pipeline(transaction=False)
            for nano in batch:
                mapping: dict[str, str | int] = {
                    "name": nano.name,
                    "ql": nano.ql,
                    "icon": nano.icon,
                    "requirements": _requirements_to_json(nano.requirements),
                }
                for field_name, value in (
                    ("description", nano.description),
                    ("school", nano.school),
                    ("strain", nano.strain),
                    ("nanocost", nano.nanocost),
                    ("ncu", nano.ncu),
                    ("crystal_id", nano.crystal_id),
                    ("duration", nano.duration),
                    ("profession", nano.profession),
                ):
                    if value is not None:
                        mapping[field_name] = value
                pipe.hset(self._nano_key(nano.id), mapping=mapping)
                pipe.zadd(self._by_name_key(), {_member(nano.name_lower, nano.id): 0})
                if nano.school:
                    pipe.hincrby(self._school_counts_key(), nano.school, 1)
            await pipe.execute()

    def _hash_to_nano(self, id_: int, h: dict[str, str]) -> NanoProgram:
        def _int_or_none(key: str) -> int | None:
            return int(h[key]) if key in h else None

        return make_nano(
            id=id_,
            name=h["name"],
            ql=int(h["ql"]),
            icon=int(h["icon"]),
            description=h.get("description"),
            school=h.get("school"),
            strain=_int_or_none("strain"),
            nanocost=_int_or_none("nanocost"),
            ncu=_int_or_none("ncu"),
            crystal_id=_int_or_none("crystal_id"),
            duration=_int_or_none("duration"),
            profession=_int_or_none("profession"),
            requirements=_requirements_from_json(h.get("requirements")),
        )

    async def _matching_ids(self, query: str) -> list[int]:
        client = get_redis()
        lo, hi = _lex_range(query.lower())
        members = await client.zrangebylex(self._by_name_key(), lo, hi)
        return [_member_id(m) for m in members]

    async def _fetch(self, ids: list[int]) -> list[NanoProgram]:
        if not ids:
            return []
        client = get_redis()
        pipe = client.pipeline(transaction=False)
        for id_ in ids:
            pipe.hgetall(self._nano_key(id_))
        hashes = await pipe.execute()
        return [self._hash_to_nano(id_, h) for id_, h in zip(ids, hashes) if h]

    async def _filtered_matches(self, query: str, ql: int, school: str, profession: int | None) -> list[NanoProgram]:
        """Pulls all name-prefix matches back and filters by ql/school/
        profession in Python - see module docstring for why. Only used when
        one of those filters is actually supplied."""
        ids = await self._matching_ids(query)
        nanos = await self._fetch(ids)
        school_needle = school.lower()
        matches = [
            nano
            for nano in nanos
            if (not ql or nano.ql == ql)
            and (not school_needle or (nano.school or "").lower() == school_needle)
            and (profession is None or nano.profession == profession)
        ]
        matches.sort(key=lambda nano: nano.name)
        return matches

    async def count(self, query: str, ql: int, school: str, profession: int | None) -> int:
        if ql or school or profession is not None:
            return len(await self._filtered_matches(query, ql, school, profession))
        client = get_redis()
        lo, hi = _lex_range(query.lower())
        return await client.zlexcount(self._by_name_key(), lo, hi)

    async def search(
        self, query: str, ql: int, school: str, profession: int | None, limit: int, offset: int = 0
    ) -> list[NanoProgram]:
        if ql or school or profession is not None:
            return (await self._filtered_matches(query, ql, school, profession))[offset : offset + limit]

        client = get_redis()
        lo, hi = _lex_range(query.lower())
        members = await client.zrangebylex(self._by_name_key(), lo, hi, start=offset, num=limit)
        ids = [_member_id(m) for m in members]
        return await self._fetch(ids)

    async def get(self, aoid: int) -> NanoProgram | None:
        client = get_redis()
        h = await client.hgetall(self._nano_key(aoid))
        if not h:
            return None
        return self._hash_to_nano(aoid, h)

    async def list_ids(self, limit: int, offset: int = 0) -> list[int]:
        """Cheap id-only enumeration (skips the per-nano hash fetch
        search() does) - used by the sitemap generator, which only needs
        ids to build /browse/nanos/{id} URLs, not full nano data."""
        client = get_redis()
        lo, hi = _lex_range("")
        members = await client.zrangebylex(self._by_name_key(), lo, hi, start=offset, num=limit)
        return [_member_id(m) for m in members]

    async def school_counts(self) -> dict[str, int]:
        """Per-school nano counts, precomputed during load() into a Redis
        hash (nanos:school_counts) so this is a single O(#schools) HGETALL
        rather than rescanning every nano per request."""
        client = get_redis()
        h = await client.hgetall(self._school_counts_key())
        return {school: int(count) for school, count in h.items()}


async def reset_all() -> None:
    """Wipes all item/nano data. Callers must do this before a fresh
    load() (main.py's startup hook does) - load() itself only ever adds/
    overwrites keys, it never removes stale ones from a previous dump."""
    await get_redis().flushdb()


# Single shared instances: the primary (app/api.py) and legacy (app/legacy.py)
# routers all read from the same Redis-backed data, loaded once (by whichever
# pod wins the startup race - see main.py) and shared by every pod thereafter.
store = ItemStore()
nano_store = NanoStore()
