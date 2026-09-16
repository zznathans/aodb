"""Item/nano-program storage, backed by MongoDB so the dataset (~65MB/125k
items, ~10.5k of them nano programs) is held once, shared across all pods,
rather than each pod loading its own full in-memory copy. Redis (see
redis_client.py) is a thin, optional cache-aside layer in front of the
expensive search()/count() queries below - Mongo is the actual source of
truth for the parsed dump.

Collections:
  items                   one doc per item, _id = aoid: name, name_lower,
                           ql, icon, description, category, subcategory,
                           effects/requirements (embedded arrays),
                           damage_min/max/critical, trigrams (see _trigrams)
  item_counts              one doc per precomputed aggregate: {"_id":
                           "category", <category>: <count>, ...},
                           {"_id": "subcategory:<category>", ...}

  nanos                    same idea as items: _id = aoid, plus school,
                           strain, nanocost, ncu, crystal_id, duration,
                           profession, profession_bucket (see
                           _profession_bucket)
  nano_counts               {"_id": "profession", "<id>": <count>, ...},
                           {"_id": "school", ...}

Name search is substring, not prefix-only - the query can match anywhere
in the name (e.g. "tank" finds "Notum Tank Armor"). A substring match isn't
expressible as a single Mongo B-tree range query the way a prefix match is,
so it's done in two steps, the same trick this store used against Redis
before the Mongo migration: a 3-character sliding-window trigram index
(`trigrams`, populated in load(), see _trigrams), backed by a multikey
index and queried with `{"trigrams": {"$all": [...]}}`, narrows the search
down to a small candidate set of docs that plausibly contain the query -
trigram co-occurrence is necessary but not sufficient for an actual
contiguous substring match, so the candidates are fetched and re-checked
for real in Python, which also gets the exact match set that trigram
membership alone can't guarantee. Query strings shorter than 3 chars can't
use the trigram index at all (there's no full 3-char window to look up) -
those fall back to scanning every doc in the narrowest available pre-built
filter (category/ql for items, everything for nanos) and checking the
substring directly in Python.

A blank query (the common "just browse" case, no search term at all) skips
substring matching entirely and becomes a plain indexed find()/
count_documents() - "everything" doesn't need a candidate search.

Items only ever filter by ql/category/subcategory; _filter_for mirrors the
narrowest-available-index selection this store used against Redis,
including its one quirk: category+subcategory together don't also apply a
ql filter at the query level (ql is still re-checked in Python for the
substring-search path, just not for the blank-query fast path) - preserved
here rather than "fixed" as part of a storage migration. Nanos additionally
filter by school/profession; unlike the old Redis implementation (which
needed a separate zset per profession to keep that filter fast), Mongo
handles arbitrary combinations of ql/school/profession_bucket as a single
indexed query, so no separate fast-path method is needed for that case
here.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pymongo.errors import BulkWriteError

from .mongo_client import get_mongo
from .redis_client import get_redis

logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000

# Below this length there's no full 3-char window to index or look up - see
# module docstring for the fallback used at that point.
_TRIGRAM_MIN_LEN = 3

# search()/count() are the only two Mongo queries expensive enough (a
# substring search pulls its whole trigram-narrowed candidate set back and
# filters/sorts in Python) to be worth caching - everything else here is
# already a cheap indexed lookup. Short TTL rather than invalidating on
# load(): dump reloads are rare (per dump version bump) and a public,
# read-only catalog tolerates up to a minute of staleness right after one
# without needing any coordination between load() and the cache.
_CACHE_TTL_SECONDS = 60


def _cache_key(*parts: object) -> str:
    # Hashed rather than joined with a delimiter so an arbitrary user
    # search string can never collide with the key structure itself.
    raw = "\x1f".join(str(p) for p in parts)
    return "cache:" + hashlib.sha256(raw.encode()).hexdigest()


async def _cached_json(key: str, compute: Callable[[], Awaitable[Any]]) -> Any:
    """Cache-aside around a JSON-serializable async computation. Redis is
    purely an optional cache in front of Mongo (the real source of truth
    for the parsed dump - see module docstring), so any Redis error here
    is logged and treated as a cache miss rather than failing the request
    - an unreachable or overloaded cache degrades to querying Mongo
    directly instead of taking the app down."""
    try:
        cached = await get_redis().get(key)
    except Exception:
        logger.warning("Redis cache read failed for %s - querying Mongo directly", key, exc_info=True)
        cached = None
    if cached is not None:
        return json.loads(cached)

    value = await compute()

    try:
        await get_redis().set(key, json.dumps(value), ex=_CACHE_TTL_SECONDS)
    except Exception:
        logger.warning("Redis cache write failed for %s - continuing without caching", key, exc_info=True)

    return value


# pymongo's duplicate-key write-error code - raised (batched into a
# BulkWriteError) by insert_many(ordered=False) for every id that's
# already present. Expected and safe to ignore: it's how load() skips ids
# it's already written on a previous call, without needing to pre-fetch
# and diff existing ids the way the Redis-backed version of this store
# used to.
_DUPLICATE_KEY_ERROR_CODE = 11000


def _trigrams(text: str) -> list[str]:
    return list({text[i : i + 3] for i in range(len(text) - 2)})


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


async def _insert_new(collection, docs: list[dict]) -> None:
    """insert_many(ordered=False) writes every doc it can and reports every
    failure at the end - duplicate-key failures (an id already loaded by a
    previous call() - see module docstring) are expected and swallowed;
    anything else re-raises."""
    for start in range(0, len(docs), _BATCH_SIZE):
        batch = docs[start : start + _BATCH_SIZE]
        try:
            await collection.insert_many(batch, ordered=False)
        except BulkWriteError as exc:
            other_errors = [
                err for err in exc.details.get("writeErrors", []) if err.get("code") != _DUPLICATE_KEY_ERROR_CODE
            ]
            if other_errors:
                raise


class ItemStore:
    def __init__(self, collection_name: str = "items", counts_collection_name: str = "item_counts") -> None:
        self._collection_name = collection_name
        self._counts_collection_name = counts_collection_name

    def _collection(self):
        return get_mongo()[self._collection_name]

    def _counts_collection(self):
        return get_mongo()[self._counts_collection_name]

    def _filter_for(self, ql: int, category: str, subcategory: str = "") -> dict:
        """Picks the narrowest applicable filter for a given ql/category/
        subcategory combination - see module docstring for the
        subcategory-drops-ql quirk this preserves from the old Redis
        implementation."""
        if category and subcategory:
            return {"category": category, "subcategory": subcategory}
        if category and ql:
            return {"category": category, "ql": ql}
        if category:
            return {"category": category}
        if ql:
            return {"ql": ql}
        return {}

    async def _ensure_indexes(self) -> None:
        collection = self._collection()
        await collection.create_index("name_lower")
        await collection.create_index([("ql", 1), ("name_lower", 1)])
        await collection.create_index([("category", 1), ("name_lower", 1)])
        await collection.create_index([("category", 1), ("ql", 1), ("name_lower", 1)])
        await collection.create_index([("category", 1), ("subcategory", 1), ("name_lower", 1)])
        await collection.create_index("trigrams")

    def _item_to_doc(self, item: Item) -> dict:
        return {
            "_id": item.id,
            "name": item.name,
            "name_lower": item.name_lower,
            "ql": item.ql,
            "icon": item.icon,
            "description": item.description,
            "category": item.category,
            "subcategory": item.subcategory,
            "effects": [e.__dict__ for e in item.effects],
            "requirements": [r.__dict__ for r in item.requirements],
            "damage_min": item.damage_min,
            "damage_max": item.damage_max,
            "damage_critical": item.damage_critical,
            "trigrams": _trigrams(item.name_lower),
        }

    def _doc_to_item(self, doc: dict) -> Item:
        return make_item(
            id=doc["_id"],
            name=doc["name"],
            ql=doc["ql"],
            icon=doc["icon"],
            description=doc.get("description"),
            category=doc.get("category", "general"),
            subcategory=doc.get("subcategory", ""),
            effects=tuple(Effect(**e) for e in doc.get("effects") or ()),
            requirements=tuple(Requirement(**r) for r in doc.get("requirements") or ()),
            damage_min=doc.get("damage_min"),
            damage_max=doc.get("damage_max"),
            damage_critical=doc.get("damage_critical"),
        )

    async def load(self, items: list[Item]) -> None:
        """Writes every item that isn't already stored - never deletes/
        replaces first. A previous load that crashed or got OOMKilled
        partway through used to leave the store either empty (if a flush
        had already run) or a confusing mix of old and new data, and every
        other pod's traffic saw that directly since nothing else guards
        reads against an in-progress load. Skipping ids that already exist
        also makes a reload of the same or overlapping dump far cheaper -
        insert_many(ordered=False) does this for free via the unique _id
        index, no pre-fetch/diff needed.

        category_counts/subcategory_counts are still computed from the
        full incoming list and written as a plain field overwrite (not
        incremented), so they stay accurate to the current dump regardless
        of which individual items were actually (re)written this run."""
        await self._ensure_indexes()

        category_counts: dict[str, int] = {}
        subcategory_counts: dict[str, dict[str, int]] = {}
        for item in items:
            category_counts[item.category] = category_counts.get(item.category, 0) + 1
            if item.subcategory:
                by_subcategory = subcategory_counts.setdefault(item.category, {})
                by_subcategory[item.subcategory] = by_subcategory.get(item.subcategory, 0) + 1

        await _insert_new(self._collection(), [self._item_to_doc(item) for item in items])

        if category_counts:
            await self._counts_collection().update_one({"_id": "category"}, {"$set": category_counts}, upsert=True)
        for category, by_subcategory in subcategory_counts.items():
            await self._counts_collection().update_one(
                {"_id": f"subcategory:{category}"}, {"$set": by_subcategory}, upsert=True
            )

    async def _substring_matches(self, query: str, ql: int, category: str, subcategory: str) -> list[Item]:
        if len(query) >= _TRIGRAM_MIN_LEN:
            candidate_filter = {"trigrams": {"$all": _trigrams(query)}}
        else:
            # Too short for the trigram index (see module docstring) - fall
            # back to the narrowest pre-built category/ql/subcategory
            # filter available and check the query in Python from there.
            candidate_filter = self._filter_for(ql, category, subcategory)
        docs = await self._collection().find(candidate_filter).to_list(length=None)
        items = [self._doc_to_item(doc) for doc in docs]
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
        async def compute() -> int:
            q = query.lower()
            if not q:
                return await self._collection().count_documents(self._filter_for(ql, category, subcategory))
            return len(await self._substring_matches(q, ql, category, subcategory))

        key = _cache_key(self._collection_name, "count", query, ql, category, subcategory)
        return await _cached_json(key, compute)

    async def search(
        self, query: str, ql: int, limit: int, offset: int = 0, category: str = "", subcategory: str = ""
    ) -> list[Item]:
        async def compute() -> list[dict]:
            q = query.lower()
            if not q:
                cursor = (
                    self._collection()
                    .find(self._filter_for(ql, category, subcategory))
                    .sort("name_lower", 1)
                    .skip(offset)
                    .limit(limit)
                )
                return [doc async for doc in cursor]
            matches = await self._substring_matches(q, ql, category, subcategory)
            return [self._item_to_doc(item) for item in matches[offset : offset + limit]]

        key = _cache_key(self._collection_name, "search", query, ql, limit, offset, category, subcategory)
        docs = await _cached_json(key, compute)
        return [self._doc_to_item(doc) for doc in docs]

    async def get(self, aoid: int) -> Item | None:
        doc = await self._collection().find_one({"_id": aoid})
        return self._doc_to_item(doc) if doc else None

    async def get_ql_variants(self, name_lower: str, description: str | None) -> list[Item]:
        """Every item sharing an exact name (not just a name-prefix) and
        description, sorted by ql - the different ql "printings" of what's
        conceptually the same item (see _merge_ql_variants in app/web.py).
        Used to interpolate an item's stats at an arbitrary ql between two
        known ones, the same way the game client does for items that only
        ever get a low-ql and high-ql dump entry."""
        cursor = self._collection().find({"name_lower": name_lower}).sort("ql", 1)
        items = [self._doc_to_item(doc) async for doc in cursor]
        return [item for item in items if item.description == description]

    async def list_ids(self, limit: int, offset: int = 0) -> list[int]:
        """Cheap id-only enumeration (skips the full-document fetch
        search() does) - used by the sitemap generator, which only needs
        ids to build /items/{id} URLs, not full item data."""
        cursor = self._collection().find({}, {"_id": 1}).sort("name_lower", 1).skip(offset).limit(limit)
        return [doc["_id"] async for doc in cursor]

    async def category_counts(self) -> dict[str, int]:
        """Per-category item counts, precomputed during load() into the
        item_counts collection ({"_id": "category", ...}) - same shape as
        NanoStore.profession_counts()."""
        doc = await self._counts_collection().find_one({"_id": "category"})
        return {k: v for k, v in (doc or {}).items() if k != "_id"}

    async def subcategory_counts(self, category: str) -> dict[str, int]:
        """Per-subcategory item counts within a single category, precomputed
        during load() into the item_counts collection ({"_id":
        "subcategory:{category}", ...}) - equip slot for "armor"/"implant",
        primary attack skill for "weapon" (see app/dump_loader.py's
        _item_subcategory() and app/web.py's
        _SUBCATEGORY_BROWSABLE_CATEGORIES)."""
        doc = await self._counts_collection().find_one({"_id": f"subcategory:{category}"})
        return {k: v for k, v in (doc or {}).items() if k != "_id"}


def _profession_bucket(nano: "NanoProgram") -> int | None:
    """Classifies a nano into the profession filter bucket it belongs to -
    the id it appears under for /nanos/professions/{slug} - or None if it
    doesn't belong to exactly one. 0 is the "no profession assigned"
    sentinel (the generic nanos every profession gets): nano.profession is
    only ever set from a "Profession exactly <id>" requirement (see
    app/dump_loader.py). The dump also carries a separate "Visual
    profession" requirement (which formula/icon variant a profession gets)
    on plenty of nanos that otherwise have no "Profession" requirement at
    all - those aren't generic either, so both attribute names are
    excluded here. Precomputed once per nano at load time into the
    profession_bucket field, so profession filtering is a plain indexed
    Mongo query rather than needing a bespoke per-profession index."""
    if nano.profession is not None:
        return nano.profession
    if any("profession" in r.attribute.lower() for r in nano.requirements):
        return None
    return 0


class NanoStore:
    def __init__(self, collection_name: str = "nanos", counts_collection_name: str = "nano_counts") -> None:
        self._collection_name = collection_name
        self._counts_collection_name = counts_collection_name

    def _collection(self):
        return get_mongo()[self._collection_name]

    def _counts_collection(self):
        return get_mongo()[self._counts_collection_name]

    def _filter_for(self, ql: int, school: str, profession: int | None) -> dict:
        filt: dict = {}
        if profession is not None:
            filt["profession_bucket"] = profession
        if school:
            filt["school"] = school
        if ql:
            filt["ql"] = ql
        return filt

    async def _ensure_indexes(self) -> None:
        collection = self._collection()
        await collection.create_index("name_lower")
        await collection.create_index([("ql", 1), ("name_lower", 1)])
        await collection.create_index([("school", 1), ("name_lower", 1)])
        await collection.create_index([("profession_bucket", 1), ("name_lower", 1)])
        await collection.create_index("trigrams")

    def _nano_to_doc(self, nano: NanoProgram) -> dict:
        return {
            "_id": nano.id,
            "name": nano.name,
            "name_lower": nano.name_lower,
            "ql": nano.ql,
            "icon": nano.icon,
            "description": nano.description,
            "school": nano.school,
            "strain": nano.strain,
            "nanocost": nano.nanocost,
            "ncu": nano.ncu,
            "crystal_id": nano.crystal_id,
            "duration": nano.duration,
            "profession": nano.profession,
            "profession_bucket": _profession_bucket(nano),
            "requirements": [r.__dict__ for r in nano.requirements],
            "effects": [e.__dict__ for e in nano.effects],
            "trigrams": _trigrams(nano.name_lower),
        }

    def _doc_to_nano(self, doc: dict) -> NanoProgram:
        return make_nano(
            id=doc["_id"],
            name=doc["name"],
            ql=doc["ql"],
            icon=doc["icon"],
            description=doc.get("description"),
            school=doc.get("school"),
            strain=doc.get("strain"),
            nanocost=doc.get("nanocost"),
            ncu=doc.get("ncu"),
            crystal_id=doc.get("crystal_id"),
            duration=doc.get("duration"),
            profession=doc.get("profession"),
            requirements=tuple(Requirement(**r) for r in doc.get("requirements") or ()),
            effects=tuple(Effect(**e) for e in doc.get("effects") or ()),
        )

    async def load(self, nanos: list[NanoProgram]) -> None:
        """Writes every nano that isn't already stored - never deletes/
        replaces first, same reasoning as ItemStore.load()."""
        await self._ensure_indexes()

        # The dump includes NPC-only buffs/effects alongside real
        # player-castable nanos - every player nano is backed by a physical
        # nano crystal item (crystal_id), which NPC-only entries lack, so
        # that's used to drop them before they're ever indexed/stored.
        # Entries with no description are dropped too - these tend to be
        # the same kind of non-player junk (a real player nano's crystal
        # always carries flavor text).
        nanos = [nano for nano in nanos if nano.crystal_id is not None and nano.description]

        # Aggregated in Python from the full incoming list and written as a
        # plain field overwrite below (not incremented) - since load()
        # never deletes/replaces existing docs first, an incremental
        # counter would keep adding onto whatever was already there across
        # repeated loads instead of reflecting the current dump.
        profession_counts: dict[int, int] = {}
        school_counts: dict[str, int] = {}
        for nano in nanos:
            bucket = _profession_bucket(nano)
            if bucket is not None:
                profession_counts[bucket] = profession_counts.get(bucket, 0) + 1
            if nano.school:
                school_counts[nano.school] = school_counts.get(nano.school, 0) + 1

        await _insert_new(self._collection(), [self._nano_to_doc(nano) for nano in nanos])

        if profession_counts:
            await self._counts_collection().update_one(
                {"_id": "profession"},
                {"$set": {str(bucket): count for bucket, count in profession_counts.items()}},
                upsert=True,
            )
        if school_counts:
            await self._counts_collection().update_one({"_id": "school"}, {"$set": school_counts}, upsert=True)

    async def _filtered_matches(self, query: str, ql: int, school: str, profession: int | None) -> list[NanoProgram]:
        """Pulls name-substring candidates back (trigram-narrowed when
        possible - see module docstring) and filters by query/ql/school/
        profession in Python. Used whenever a non-blank query is supplied -
        a substring match can't be pushed down as a bounded Mongo range
        query the way a prefix match can, so this always fetches the full
        candidate set, filters, sorts, and only slices for the page at the
        very end (see search()).

        profession=0 is a sentinel meaning "no profession assigned" (the
        generic nanos every profession gets) - 0 is never a real profession
        id (see app/professions.py's PROFESSION_NAMES), so it's free to
        reuse rather than adding a separate parameter. See
        _profession_bucket for the classification rule itself."""
        q = query.lower()
        if len(q) >= _TRIGRAM_MIN_LEN:
            candidate_filter = {"trigrams": {"$all": _trigrams(q)}}
        else:
            # Too short for the trigram index (see module docstring) -
            # there's no other pre-built filter for nanos, so fall back to
            # everything.
            candidate_filter = {}
        docs = await self._collection().find(candidate_filter).to_list(length=None)
        nanos = [self._doc_to_nano(doc) for doc in docs]
        school_needle = school.lower()

        matches = [
            nano
            for nano in nanos
            if (not q or q in nano.name_lower)
            and (not ql or nano.ql == ql)
            and (not school_needle or (nano.school or "").lower() == school_needle)
            and (profession is None or _profession_bucket(nano) == profession)
        ]
        matches.sort(key=lambda nano: nano.name)
        return matches

    async def count(self, query: str, ql: int, school: str, profession: int | None) -> int:
        async def compute() -> int:
            q = query.lower()
            if not q:
                return await self._collection().count_documents(self._filter_for(ql, school, profession))
            return len(await self._filtered_matches(q, ql, school, profession))

        key = _cache_key(self._collection_name, "count", query, ql, school, profession)
        return await _cached_json(key, compute)

    async def search(
        self, query: str, ql: int, school: str, profession: int | None, limit: int, offset: int = 0
    ) -> list[NanoProgram]:
        async def compute() -> list[dict]:
            q = query.lower()
            if not q:
                cursor = (
                    self._collection()
                    .find(self._filter_for(ql, school, profession))
                    .sort("name_lower", 1)
                    .skip(offset)
                    .limit(limit)
                )
                return [doc async for doc in cursor]
            matches = await self._filtered_matches(q, ql, school, profession)
            return [self._nano_to_doc(nano) for nano in matches[offset : offset + limit]]

        key = _cache_key(self._collection_name, "search", query, ql, school, profession, limit, offset)
        docs = await _cached_json(key, compute)
        return [self._doc_to_nano(doc) for doc in docs]

    async def get(self, aoid: int) -> NanoProgram | None:
        doc = await self._collection().find_one({"_id": aoid})
        return self._doc_to_nano(doc) if doc else None

    async def list_ids(self, limit: int, offset: int = 0) -> list[int]:
        """Cheap id-only enumeration (skips the full-document fetch
        search() does) - used by the sitemap generator, which only needs
        ids to build /nanos/{id} URLs, not full nano data."""
        cursor = self._collection().find({}, {"_id": 1}).sort("name_lower", 1).skip(offset).limit(limit)
        return [doc["_id"] async for doc in cursor]

    async def school_counts(self) -> dict[str, int]:
        """Per-school nano counts, precomputed during load() into the
        nano_counts collection ({"_id": "school", ...}) so this is a single
        cheap find_one rather than rescanning every nano per request."""
        doc = await self._counts_collection().find_one({"_id": "school"})
        return {k: v for k, v in (doc or {}).items() if k != "_id"}

    async def profession_counts(self) -> dict[int, int]:
        """Per-profession nano counts, precomputed during load() into the
        nano_counts collection ({"_id": "profession", ...}) - same shape as
        school_counts()."""
        doc = await self._counts_collection().find_one({"_id": "profession"})
        return {int(k): v for k, v in (doc or {}).items() if k != "_id"}


# Single shared instances: the primary (app/api.py) and legacy (app/legacy.py)
# routers all read from the same Mongo-backed data, loaded once (by whichever
# pod wins the startup race - see main.py) and shared by every pod thereafter.
store = ItemStore()
nano_store = NanoStore()
