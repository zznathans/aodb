"""The primary JSON API for item/nano search and lookup, independent of the
legacy AOML contract (GET /legacy) - see legacy.py. Simple query
params or a JSON body in, structured JSON out, real HTTP status codes."""

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .store import Item, NanoProgram, nano_store, store

router = APIRouter()


class ItemOut(BaseModel):
    id: int
    name: str
    ql: int
    icon: int
    description: str | None

    @classmethod
    def from_item(cls, item: Item) -> "ItemOut":
        return cls(id=item.id, name=item.name, ql=item.ql, icon=item.icon, description=item.description)


class ItemQuery(BaseModel):
    q: str = ""
    ql: int = 0
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


async def _search(query: ItemQuery, response: Response) -> list[ItemOut]:
    total = await store.count(query.q, query.ql)
    items = await store.search(query.q, query.ql, query.limit, query.offset)
    response.headers["X-Total-Count"] = str(total)
    return [ItemOut.from_item(item) for item in items]


@router.get("/items")
async def search_items_get(
    response: Response,
    q: str = Query(default=""),
    ql: int = Query(default=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ItemOut]:
    return await _search(ItemQuery(q=q, ql=ql, limit=limit, offset=offset), response)


@router.post("/items")
async def search_items_post(query: ItemQuery, response: Response) -> list[ItemOut]:
    return await _search(query, response)


@router.get("/items/{aoid}")
async def get_item(aoid: int) -> ItemOut:
    item = await store.get(aoid)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No item with id {aoid}")
    return ItemOut.from_item(item)


class RequirementOut(BaseModel):
    attribute: str
    operator: str
    value: str


class NanoOut(BaseModel):
    id: int
    name: str
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
    requirements: list[RequirementOut]

    @classmethod
    def from_nano(cls, nano: NanoProgram) -> "NanoOut":
        return cls(
            id=nano.id,
            name=nano.name,
            ql=nano.ql,
            icon=nano.icon,
            description=nano.description,
            school=nano.school,
            strain=nano.strain,
            nanocost=nano.nanocost,
            ncu=nano.ncu,
            crystal_id=nano.crystal_id,
            duration=nano.duration,
            profession=nano.profession,
            requirements=[RequirementOut(**r.__dict__) for r in nano.requirements],
        )


class NanoQuery(BaseModel):
    q: str = ""
    ql: int = 0
    school: str = ""
    profession: int | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


async def _search_nanos(query: NanoQuery, response: Response) -> list[NanoOut]:
    total = await nano_store.count(query.q, query.ql, query.school, query.profession)
    nanos = await nano_store.search(query.q, query.ql, query.school, query.profession, query.limit, query.offset)
    response.headers["X-Total-Count"] = str(total)
    return [NanoOut.from_nano(nano) for nano in nanos]


@router.get("/nanos")
async def search_nanos_get(
    response: Response,
    q: str = Query(default=""),
    ql: int = Query(default=0),
    school: str = Query(default="", description="Exact match, e.g. Combat, Healing, Psionic, Space, Protection"),
    profession: int | None = Query(default=None, description="Raw numeric profession id from the dump"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[NanoOut]:
    query = NanoQuery(q=q, ql=ql, school=school, profession=profession, limit=limit, offset=offset)
    return await _search_nanos(query, response)


@router.post("/nanos")
async def search_nanos_post(query: NanoQuery, response: Response) -> list[NanoOut]:
    return await _search_nanos(query, response)


@router.get("/nanos/{aoid}")
async def get_nano(aoid: int) -> NanoOut:
    nano = await nano_store.get(aoid)
    if nano is None:
        raise HTTPException(status_code=404, detail=f"No nano program with id {aoid}")
    return NanoOut.from_nano(nano)
