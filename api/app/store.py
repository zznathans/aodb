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
class Effect:
    hook: str
    target: str
    action: str
    attribute: str | None
    value: str | None


@dataclass(frozen=True)
class Requirement:
    hook: str
    attribute: str
    operator: str
    value: str


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    name_lower: str
    ql: int
    icon: int
    description: str | None
    category: str
    subcategory: str = ""
    effects: tuple[Effect, ...] = field(default_factory=tuple)
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)
    damage_min: int | None = None
    damage_max: int | None = None
    damage_critical: int | None = None


def make_item(
    id: int,
    name: str,
    ql: int = 0,
    icon: int = 0,
    description: str | None = None,
    category: str = "general",
    subcategory: str = "",
    effects: tuple[Effect, ...] = (),
    requirements: tuple[Requirement, ...] = (),
    damage_min: int | None = None,
    damage_max: int | None = None,
    damage_critical: int | None = None,
) -> Item:
    return Item(
        id=id,
        name=name,
        name_lower=name.lower(),
        ql=ql,
        icon=icon,
        description=description,
        category=category,
        subcategory=subcategory,
        effects=effects,
        requirements=requirements,
        damage_min=damage_min,
        damage_max=damage_max,
        damage_critical=damage_critical,
    )


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
    effects: tuple[Effect, ...] = field(default_factory=tuple)


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
    effects: tuple[Effect, ...] = (),
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
        effects=effects,
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

    def _by_name_category_key(self, category: str) -> str:
        return f"{self._prefix}:by_name:category:{category}"

    def _by_name_category_ql_key(self, category: str, ql: int) -> str:
        return f"{self._prefix}:by_name:category:{category}:ql:{ql}"

    def _by_name_category_subcategory_key(self, category: str, subcategory: str) -> str:
        return f"{self._prefix}:by_name:category:{category}:subcategory:{subcategory}"

    def _category_counts_key(self) -> str:
        return f"{self._prefix}:category_counts"

    def _subcategory_counts_key(self, category: str) -> str:
        return f"{self._prefix}:subcategory_counts:{category}"

    def _key_for(self, ql: int, category: str, subcategory: str = "") -> str:
        """Picks the narrowest pre-built index for a given ql/category/
        subcategory combination - mirrors the ql-only indexing already used
        here (see module docstring) rather than ever pulling the full item
        set back to filter in Python, which wouldn't scale at this store's
        size."""
        if category and subcategory:
            return self._by_name_category_subcategory_key(category, subcategory)
        if category and ql:
            return self._by_name_category_ql_key(category, ql)
        if category:
            return self._by_name_category_key(category)
        if ql:
            return self._by_name_ql_key(ql)
        return self._by_name_key()

    async def load(self, items: list[Item]) -> None:
        client = get_redis()

        category_counts: dict[str, int] = {}
        subcategory_counts: dict[str, dict[str, int]] = {}
        for item in items:
            category_counts[item.category] = category_counts.get(item.category, 0) + 1
            if item.subcategory:
                by_subcategory = subcategory_counts.setdefault(item.category, {})
                by_subcategory[item.subcategory] = by_subcategory.get(item.subcategory, 0) + 1

        for start in range(0, len(items), _BATCH_SIZE):
            batch = items[start : start + _BATCH_SIZE]
            pipe = client.pipeline(transaction=False)
            for item in batch:
                mapping: dict[str, str | int] = {
                    "name": item.name,
                    "ql": item.ql,
                    "icon": item.icon,
                    "category": item.category,
                    "effects": _effects_to_json(item.effects),
                    "requirements": _requirements_to_json(item.requirements),
                }
                if item.description is not None:
                    mapping["description"] = item.description
                if item.subcategory:
                    mapping["subcategory"] = item.subcategory
                if item.damage_min is not None:
                    mapping["damage_min"] = item.damage_min
                if item.damage_max is not None:
                    mapping["damage_max"] = item.damage_max
                if item.damage_critical is not None:
                    mapping["damage_critical"] = item.damage_critical
                pipe.hset(self._item_key(item.id), mapping=mapping)

                member = _member(item.name_lower, item.id)
                pipe.zadd(self._by_name_key(), {member: 0})
                if item.ql:
                    pipe.zadd(self._by_name_ql_key(item.ql), {member: 0})
                pipe.zadd(self._by_name_category_key(item.category), {member: 0})
                if item.ql:
                    pipe.zadd(self._by_name_category_ql_key(item.category, item.ql), {member: 0})
                if item.subcategory:
                    pipe.zadd(self._by_name_category_subcategory_key(item.category, item.subcategory), {member: 0})
            await pipe.execute()

        if category_counts:
            await client.hset(self._category_counts_key(), mapping=category_counts)
        for category, by_subcategory in subcategory_counts.items():
            await client.hset(self._subcategory_counts_key(category), mapping=by_subcategory)

    def _hash_to_item(self, id_: int, h: dict[str, str]) -> Item:
        def _int_or_none(key: str) -> int | None:
            return int(h[key]) if key in h else None

        return make_item(
            id=id_,
            name=h["name"],
            ql=int(h["ql"]),
            icon=int(h["icon"]),
            description=h.get("description"),
            category=h.get("category", "general"),
            subcategory=h.get("subcategory", ""),
            effects=_effects_from_json(h.get("effects")),
            requirements=_requirements_from_json(h.get("requirements")),
            damage_min=_int_or_none("damage_min"),
            damage_max=_int_or_none("damage_max"),
            damage_critical=_int_or_none("damage_critical"),
        )

    async def count(self, query: str, ql: int, category: str = "", subcategory: str = "") -> int:
        client = get_redis()
        key = self._key_for(ql, category, subcategory)
        lo, hi = _lex_range(query.lower())
        return await client.zlexcount(key, lo, hi)

    async def search(
        self, query: str, ql: int, limit: int, offset: int = 0, category: str = "", subcategory: str = ""
    ) -> list[Item]:
        client = get_redis()
        key = self._key_for(ql, category, subcategory)
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

    async def get_ql_variants(self, name_lower: str, description: str | None) -> list[Item]:
        """Every item sharing an exact name (not just a name-prefix) and
        description, sorted by ql - the different ql "printings" of what's
        conceptually the same item (see _merge_ql_variants in app/web.py).
        Used to interpolate an item's stats at an arbitrary ql between two
        known ones, the same way the game client does for items that only
        ever get a low-ql and high-ql dump entry."""
        client = get_redis()
        lo, hi = f"[{name_lower}{_NUL}", f"({name_lower}{_NUL}\xff"
        members = await client.zrangebylex(self._by_name_key(), lo, hi)
        ids = [_member_id(m) for m in members]
        if not ids:
            return []
        pipe = client.pipeline(transaction=False)
        for id_ in ids:
            pipe.hgetall(self._item_key(id_))
        hashes = await pipe.execute()
        items = [self._hash_to_item(id_, h) for id_, h in zip(ids, hashes) if h]
        variants = [item for item in items if item.description == description]
        variants.sort(key=lambda item: item.ql)
        return variants

    async def list_ids(self, limit: int, offset: int = 0) -> list[int]:
        """Cheap id-only enumeration (skips the per-item hash fetch
        search() does) - used by the sitemap generator, which only needs
        ids to build /items/{id} URLs, not full item data."""
        client = get_redis()
        lo, hi = _lex_range("")
        members = await client.zrangebylex(self._by_name_key(), lo, hi, start=offset, num=limit)
        return [_member_id(m) for m in members]

    async def category_counts(self) -> dict[str, int]:
        """Per-category item counts, precomputed during load() into a
        Redis hash (items:category_counts) - same shape as
        NanoStore.profession_counts()."""
        client = get_redis()
        h = await client.hgetall(self._category_counts_key())
        return {category: int(count) for category, count in h.items()}

    async def subcategory_counts(self, category: str) -> dict[str, int]:
        """Per-subcategory item counts within a single category, precomputed
        during load() into a Redis hash (items:subcategory_counts:{category})
        - equip slot for "armor"/"implant", primary attack skill for
        "weapon" (see app/dump_loader.py's _item_subcategory() and
        app/web.py's _SUBCATEGORY_BROWSABLE_CATEGORIES)."""
        client = get_redis()
        h = await client.hgetall(self._subcategory_counts_key(category))
        return {subcategory: int(count) for subcategory, count in h.items()}


def _requirements_to_json(requirements: tuple[Requirement, ...]) -> str:
    return json.dumps([r.__dict__ for r in requirements])


def _requirements_from_json(raw: str | None) -> tuple[Requirement, ...]:
    if not raw:
        return ()
    return tuple(Requirement(**r) for r in json.loads(raw))


def _effects_to_json(effects: tuple[Effect, ...]) -> str:
    return json.dumps([e.__dict__ for e in effects])


def _effects_from_json(raw: str | None) -> tuple[Effect, ...]:
    if not raw:
        return ()
    return tuple(Effect(**e) for e in json.loads(raw))


class NanoStore:
    def __init__(self, key_prefix: str = "nanos") -> None:
        self._prefix = key_prefix

    def _nano_key(self, id_: int) -> str:
        return f"nano:{id_}"

    def _by_name_key(self) -> str:
        return f"{self._prefix}:by_name"

    def _school_counts_key(self) -> str:
        return f"{self._prefix}:school_counts"

    def _profession_counts_key(self) -> str:
        return f"{self._prefix}:profession_counts"

    async def load(self, nanos: list[NanoProgram]) -> None:
        client = get_redis()

        # The dump includes NPC-only buffs/effects alongside real
        # player-castable nanos - every player nano is backed by a physical
        # nano crystal item (crystal_id), which NPC-only entries lack, so
        # that's used to drop them before they're ever indexed/stored.
        # Entries with no description are dropped too - these tend to be
        # the same kind of non-player junk (a real player nano's crystal
        # always carries flavor text).
        nanos = [nano for nano in nanos if nano.crystal_id is not None and nano.description]

        # Aggregated in Python (the full nano list is already in memory for
        # this call) rather than with per-item Redis commands.
        profession_counts: dict[int, int] = {}
        for nano in nanos:
            if nano.profession is not None:
                profession_counts[nano.profession] = profession_counts.get(nano.profession, 0) + 1

        for start in range(0, len(nanos), _BATCH_SIZE):
            batch = nanos[start : start + _BATCH_SIZE]
            pipe = client.pipeline(transaction=False)
            for nano in batch:
                mapping: dict[str, str | int] = {
                    "name": nano.name,
                    "ql": nano.ql,
                    "icon": nano.icon,
                    "requirements": _requirements_to_json(nano.requirements),
                    "effects": _effects_to_json(nano.effects),
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

        if profession_counts:
            await client.hset(self._profession_counts_key(), mapping=profession_counts)

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
            effects=_effects_from_json(h.get("effects")),
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
        one of those filters is actually supplied.

        profession=0 is a sentinel meaning "no profession assigned" (the
        generic nanos every profession gets) - 0 is never a real profession
        id (see app/professions.py's PROFESSION_NAMES), so it's free to
        reuse rather than adding a separate parameter."""
        ids = await self._matching_ids(query)
        nanos = await self._fetch(ids)
        school_needle = school.lower()

        def _profession_matches(nano: NanoProgram) -> bool:
            if profession is None:
                return True
            if profession == 0:
                # nano.profession is only ever set from a "Profession
                # exactly <id>" requirement (see app/dump_loader.py). The
                # dump also carries a separate "Visual profession"
                # requirement (which formula/icon variant a profession
                # gets) on plenty of nanos that otherwise have no
                # "Profession" requirement at all - those aren't generic
                # either, so both attribute names are excluded here.
                return nano.profession is None and not any(
                    "profession" in r.attribute.lower() for r in nano.requirements
                )
            return nano.profession == profession

        matches = [
            nano
            for nano in nanos
            if (not ql or nano.ql == ql)
            and (not school_needle or (nano.school or "").lower() == school_needle)
            and _profession_matches(nano)
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
        ids to build /nanos/{id} URLs, not full nano data."""
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

    async def profession_counts(self) -> dict[int, int]:
        """Per-profession nano counts, precomputed during load() into a
        Redis hash (nanos:profession_counts) - same shape as school_counts()."""
        client = get_redis()
        h = await client.hgetall(self._profession_counts_key())
        return {int(profession): int(count) for profession, count in h.items()}


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
