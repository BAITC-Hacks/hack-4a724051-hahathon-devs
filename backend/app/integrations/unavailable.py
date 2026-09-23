from app.core.errors import unavailable


class UnavailableCatalog:
    async def search(self, query, limit):
        raise unavailable()

    async def get_product(self, product_id):
        raise unavailable()

    async def alternatives(self, product_id):
        raise unavailable()


class UnavailableDocuments:
    async def resolve(self, session_id, asset_ids):
        if asset_ids:
            raise unavailable()
        return []


class UnavailableActions:
    async def get_cart(self, session_id):
        raise unavailable()

    async def propose(self, session_id, payload, key):
        raise unavailable()

    async def get_proposal(self, session_id, proposal_id):
        raise unavailable()

    async def confirm(self, session_id, proposal_id, version, key):
        raise unavailable()

    async def reject(self, session_id, proposal_id):
        raise unavailable()
