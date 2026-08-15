"""Item/nano-program storage, backed by Redis so the dataset (~65MB/125k
items, ~10.5k of them nano programs) is held once, shared across all pods,
rather than each pod loading its own full in-memory copy.

Key schema (all keys use decode_responses=True string values):
  item:{id}                    hash: name, ql, icon, description?
  items:by_name                zset: member "{name_lower}\\0{id}", score 0
  items:by_name:ql:{ql}        zset: same members, items with that exact ql
  items:trigram:{trigram}      set: item ids whose name contains that
                                3-char substring (see _trigrams below)

  nano:{id}                    hash: name, ql, icon, description?, school?,
                                strain?, nanocost?, ncu?, crystal_id?,
                                duration?, profession?, requirements (JSON)
  nanos:by_name                zset: member "{name_lower}\\0{id}", score 0
  nanos:trigram:{trigram}      set: nano ids, same idea as items:trigram:*

Name search is substring, not prefix-only - the query can match anywhere
in the name (e.g. "tank" finds "Notum Tank Armor"), matching what the old
in-memory implementation used to do. A substring match isn't expressible
as a single efficient Redis range query the way a prefix match is
(ZRANGEBYLEX), so it's done in two steps: a 3-character sliding-window
trigram index (populated in load(), see _trigrams) narrows the search down
to a small candidate set of ids that plausibly contain the query - trigram
co-occurrence is necessary but not sufficient for an actual contiguous
substring match, so the candidates are fetched and re-checked for real in
Python, which also gets the exact match set that different-order trigram
intersections alone can't guarantee. Query strings shorter than 3 chars
can't use the trigram index at all (there's no full 3-char window to look
up) - those fall back to scanning every id in the narrowest available
pre-built index (category/ql for items, everything for nanos) and checking
the substring directly in Python. The same trade-off full-text search
engines make for sub-trigram-length queries; short queries are rare and
the fallback is still bounded by whatever category/ql narrowing is
available.

A blank query (the common "just browse" case, no search term at all) skips
substring matching entirely and keeps the previous prefix-range behavior
(ZRANGEBYLEX over the whole key), since "everything" doesn't need a
candidate search - this keeps plain pagination as cheap as before.

Items only ever filter by ql, so a per-ql zset gives an efficient combined
(prefix + ql) index, reused as the substring fallback's candidate source
for sub-trigram-length queries. Nanos additionally filter by school/
profession, which aren't practical to pre-index for every combination;
whenever a query or any of those filters is supplied, matches are pulled
back (trigram-narrowed when possible) and filtered by the rest in Python
before sorting/paging.
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

# Below this length there's no full 3-char window to index or look up - see
# module docstring for the fallback used at that point.
_TRIGRAM_MIN_LEN = 3


def _trigrams(text: str) -> set[str]:
    return {text[i : i + 3] for i in range(len(text) - 2)}


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

    def _trigram_key(self, trigram: str) -> str:
        return f"{self._prefix}:trigram:{trigram}"

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
        """Writes every item that isn't already in Redis - never flushes
        first. A previous load that crashed or got OOMKilled partway
        through used to leave the store either empty (if a flush had
        already run) or a confusing mix of old and new data, and every
        other pod's traffic saw that directly since nothing else guards
        reads against an in-progress load. Skipping ids that already exist
        also makes a reload of the same or overlapping dump far cheaper -
        every Redis write here (HSET/ZADD/SADD) is idempotent anyway, so
        this is purely about not re-doing the work, not correctness.

        category_counts/subcategory_counts are still computed from the
        full incoming list and written as a plain overwrite (not
        incremented), so they stay accurate to the current dump regardless
        of which individual items were actually (re)written this run."""
        client = get_redis()

        category_counts: dict[str, int] = {}
        subcategory_counts: dict[str, dict[str, int]] = {}
        for item in items:
            category_counts[item.category] = category_counts.get(item.category, 0) + 1
            if item.subcategory:
                by_subcategory = subcategory_counts.setdefault(item.category, {})
                by_subcategory[item.subcategory] = by_subcategory.get(item.subcategory, 0) + 1

        existing_ids = {_member_id(m) for m in await client.zrange(self._by_name_key(), 0, -1)}
        new_items = [item for item in items if item.id not in existing_ids]

        for start in range(0, len(new_items), _BATCH_SIZE):
            batch = new_items[start : start + _BATCH_SIZE]
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
                for trigram in _trigrams(item.name_lower):
                    pipe.sadd(self._trigram_key(trigram), item.id)
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

    async def _fetch_many(self, ids: list[int]) -> list[Item]:
        if not ids:
            return []
        client = get_redis()
        pipe = client.pipeline(transaction=False)
        for id_ in ids:
            pipe.hgetall(self._item_key(id_))
        hashes = await pipe.execute()
        return [self._hash_to_item(id_, h) for id_, h in zip(ids, hashes) if h]

    async def _trigram_candidate_ids(self, query: str) -> list[int]:
        client = get_redis()
        keys = [self._trigram_key(t) for t in _trigrams(query)]
        members = await client.smembers(keys[0]) if len(keys) == 1 else await client.sinter(keys)
        return [int(m) for m in members]

    async def _candidate_ids(self, query: str, ql: int, category: str, subcategory: str) -> list[int]:
        if len(query) >= _TRIGRAM_MIN_LEN:
            return await self._trigram_candidate_ids(query)
        # Too short for the trigram index (see module docstring) - fall
        # back to the narrowest pre-built category/ql/subcategory index
        # available and check the query in Python from there.
        client = get_redis()
        key = self._key_for(ql, category, subcategory)
        members = await client.zrange(key, 0, -1)
        return [_member_id(m) for m in members]

    async def _substring_matches(self, query: str, ql: int, category: str, subcategory: str) -> list[Item]:
        ids = await self._candidate_ids(query, ql, category, subcategory)
        items = await self._fetch_many(ids)
        matches = [
            item
            for item in items
            if query in item.name_lower
            and (not category or item.category == category)
            and (not subcategory or item.subcategory == subcategory)
            and (not ql or item.ql == ql)
        ]
        matches.sort(key=lambda item: item.name_lower)
        return matches

    async def count(self, query: str, ql: int, category: str = "", subcategory: str = "") -> int:
        q = query.lower()
        if not q:
            client = get_redis()
            key = self._key_for(ql, category, subcategory)
            lo, hi = _lex_range("")
            return await client.zlexcount(key, lo, hi)
        return len(await self._substring_matches(q, ql, category, subcategory))

    async def search(
        self, query: str, ql: int, limit: int, offset: int = 0, category: str = "", subcategory: str = ""
    ) -> list[Item]:
        q = query.lower()
        if not q:
            client = get_redis()
            key = self._key_for(ql, category, subcategory)
            lo, hi = _lex_range("")
            members = await client.zrangebylex(key, lo, hi, start=offset, num=limit)
            return await self._fetch_many([_member_id(m) for m in members])
        matches = await self._substring_matches(q, ql, category, subcategory)
        return matches[offset : offset + limit]

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
        items = await self._fetch_many([_member_id(m) for m in members])
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

    def _trigram_key(self, trigram: str) -> str:
        return f"{self._prefix}:trigram:{trigram}"

    def _school_counts_key(self) -> str:
        return f"{self._prefix}:school_counts"

    def _profession_counts_key(self) -> str:
        return f"{self._prefix}:profession_counts"

    async def load(self, nanos: list[NanoProgram]) -> None:
        """Writes every nano that isn't already in Redis - never flushes
        first, same reasoning as ItemStore.load()."""
        client = get_redis()

        # The dump includes NPC-only buffs/effects alongside real
        # player-castable nanos - every player nano is backed by a physical
        # nano crystal item (crystal_id), which NPC-only entries lack, so
        # that's used to drop them before they're ever indexed/stored.
        # Entries with no description are dropped too - these tend to be
        # the same kind of non-player junk (a real player nano's crystal
        # always carries flavor text).
        nanos = [nano for nano in nanos if nano.crystal_id is not None and nano.description]

        # Aggregated in Python from the full incoming list and written as a
        # plain overwrite below (not HINCRBY'd per-item as school_counts
        # used to be) - since load() no longer flushes first, an
        # incremental counter would keep adding onto whatever was already
        # there across repeated loads instead of reflecting the current
        # dump.
        profession_counts: dict[int, int] = {}
        school_counts: dict[str, int] = {}
        for nano in nanos:
            if nano.profession is not None:
                profession_counts[nano.profession] = profession_counts.get(nano.profession, 0) + 1
            if nano.school:
                school_counts[nano.school] = school_counts.get(nano.school, 0) + 1

        existing_ids = {_member_id(m) for m in await client.zrange(self._by_name_key(), 0, -1)}
        new_nanos = [nano for nano in nanos if nano.id not in existing_ids]

        for start in range(0, len(new_nanos), _BATCH_SIZE):
            batch = new_nanos[start : start + _BATCH_SIZE]
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
                for trigram in _trigrams(nano.name_lower):
                    pipe.sadd(self._trigram_key(trigram), nano.id)
            await pipe.execute()

        if profession_counts:
            await client.hset(self._profession_counts_key(), mapping=profession_counts)
        if school_counts:
            await client.hset(self._school_counts_key(), mapping=school_counts)

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

    async def _trigram_candidate_ids(self, query: str) -> list[int]:
        client = get_redis()
        keys = [self._trigram_key(t) for t in _trigrams(query)]
        members = await client.smembers(keys[0]) if len(keys) == 1 else await client.sinter(keys)
        return [int(m) for m in members]

    async def _candidate_ids(self, query: str) -> list[int]:
        if len(query) >= _TRIGRAM_MIN_LEN:
            return await self._trigram_candidate_ids(query)
        # Too short for the trigram index (see module docstring) - there's
        # no other pre-built index for nanos, so fall back to everything.
        client = get_redis()
        lo, hi = _lex_range("")
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
        """Pulls name-substring candidates back (trigram-narrowed when
        possible - see module docstring) and filters by query/ql/school/
        profession in Python. Used whenever a query or any of those filters
        is supplied - a substring match can't be pushed down as a bounded
        Redis range query the way a prefix match can, so this always
        fetches the full candidate set, filters, sorts, and only slices for
        the page at the very end (see search()).

        profession=0 is a sentinel meaning "no profession assigned" (the
        generic nanos every profession gets) - 0 is never a real profession
        id (see app/professions.py's PROFESSION_NAMES), so it's free to
        reuse rather than adding a separate parameter."""
        q = query.lower()
        ids = await self._candidate_ids(q)
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
            if (not q or q in nano.name_lower)
            and (not ql or nano.ql == ql)
            and (not school_needle or (nano.school or "").lower() == school_needle)
            and _profession_matches(nano)
        ]
        matches.sort(key=lambda nano: nano.name)
        return matches

    async def count(self, query: str, ql: int, school: str, profession: int | None) -> int:
        if not query and not ql and not school and profession is None:
            client = get_redis()
            lo, hi = _lex_range("")
            return await client.zlexcount(self._by_name_key(), lo, hi)
        return len(await self._filtered_matches(query, ql, school, profession))

    async def search(
        self, query: str, ql: int, school: str, profession: int | None, limit: int, offset: int = 0
    ) -> list[NanoProgram]:
        if not query and not ql and not school and profession is None:
            client = get_redis()
            lo, hi = _lex_range("")
            members = await client.zrangebylex(self._by_name_key(), lo, hi, start=offset, num=limit)
            ids = [_member_id(m) for m in members]
            return await self._fetch(ids)

        return (await self._filtered_matches(query, ql, school, profession))[offset : offset + limit]

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


# Single shared instances: the primary (app/api.py) and legacy (app/legacy.py)
# routers all read from the same Redis-backed data, loaded once (by whichever
# pod wins the startup race - see main.py) and shared by every pod thereafter.
store = ItemStore()
nano_store = NanoStore()
