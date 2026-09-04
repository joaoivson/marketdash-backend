"""Quais números a campanha usa — vínculo N:N campanha↔instância (espelho da 079)."""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.campanha_grupos import CampanhaGrupo, CampanhaNumero
from app.models.whatsapp_grupos import WhatsappGrupo, WhatsappGrupoInstancia


class CampanhaNumeroRepository:
    def __init__(self, db: Session):
        self.db = db

    def instancia_ids(self, campanha_id: int) -> List[int]:
        return [
            iid for (iid,) in
            self.db.query(CampanhaNumero.instancia_id)
            .filter(CampanhaNumero.campanha_id == campanha_id).all()
        ]

    def definir(self, campanha_id: int, instancia_ids: List[int]) -> None:
        """Substitui o conjunto de números (multi-select da aba)."""
        atuais = {
            v.instancia_id: v for v in
            self.db.query(CampanhaNumero)
            .filter(CampanhaNumero.campanha_id == campanha_id).all()
        }
        desejados = set(instancia_ids)
        for iid in desejados - set(atuais):
            self.db.add(CampanhaNumero(campanha_id=campanha_id, instancia_id=iid))
        for iid, vinculo in atuais.items():
            if iid not in desejados:
                self.db.delete(vinculo)

    def grupos_por_instancia(self, campanha_id: int) -> Dict[int, Dict[int, str]]:
        """
        instancia_id → {grupo_id: nome} dos grupos DESTA campanha que ela alcança.

        Chaveado por **id**, não por nome: a remoção precisa comparar conjuntos
        de grupos entre números, e dois grupos homônimos ("Promos #1" em dois
        chips) colapsariam num só se a chave fosse o nome — deixando passar a
        remoção que órfã um deles.

        O nome vem junto porque é o que a mensagem de erro mostra: bloquear sem
        dizer quais grupos travam a ação deixa a afiliada sem o próximo passo.
        """
        linhas = (
            self.db.query(WhatsappGrupoInstancia.instancia_id,
                          WhatsappGrupo.id, WhatsappGrupo.nome)
            .join(WhatsappGrupo, WhatsappGrupo.id == WhatsappGrupoInstancia.grupo_id)
            .join(CampanhaGrupo, CampanhaGrupo.grupo_id == WhatsappGrupo.id)
            .filter(CampanhaGrupo.campanha_id == campanha_id)
            .all()
        )
        resultado: Dict[int, Dict[int, str]] = {}
        for instancia_id, grupo_id, nome in linhas:
            resultado.setdefault(instancia_id, {})[grupo_id] = nome or f"Grupo {grupo_id}"
        return resultado

    def contagem_de_grupos(self, campanha_id: int) -> Dict[int, int]:
        """instancia_id → quantos grupos da campanha ela tem (coluna da aba)."""
        return {
            iid: len(nomes)
            for iid, nomes in self.grupos_por_instancia(campanha_id).items()
        }
