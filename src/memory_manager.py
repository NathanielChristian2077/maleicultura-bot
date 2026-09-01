from datetime import datetime, timedelta
from langchain.memory import ConversationSummaryBufferMemory
from typing import Dict

class MemoryManager:
    """
    Gerencia a memória das conversas com as regras do WhatsApp e adiciona
    um mecanismo de resumo automático para conversas longas.
    """
    def __init__(self, llm_for_summarization, max_token_limit: int = 2000, max_session_duration_hours: int = 24):
        self.store: Dict[str, Dict] = {}
        self.llm = llm_for_summarization
        self.max_token_limit = max_token_limit
        self.session_timeout = timedelta(hours=max_session_duration_hours)

    def get_memory(self, user_number: str) -> ConversationSummaryBufferMemory:
        """
        Retorna a memória da conversa com base no id de sessão
        """
        current_time = datetime.now()
        
        if user_number in self.store:
            session = self.store[user_number]
            last_interaction_time = session["last_interaction"]

            # Checa se ultima interação foi a 24h
            if current_time - last_interaction_time > self.session_timeout:
                print(f"\n[Info] Sessão para {user_number} expirou. Reiniciando histórico com resumo.")
                self._create_new_session(user_number, current_time)
            else:
                session["last_interaction"] = current_time
        else:
            print(f"\n[Info] Nova sessão criada para {user_number} com resumo.")
            self._create_new_session(user_number, current_time)
            
        return self.store[user_number]["memory"]
    
    def _create_new_session(self, user_number: str, timestamp: datetime):
        """Método auxiliar para criar uma nova entrada de sessão com memória de resumo."""
        self.store[user_number] = {
            "memory": ConversationSummaryBufferMemory(
                llm=self.llm,
                max_token_limit=self.max_token_limit,
                return_messages=True,
                memory_key="history"
            ),
            "last_interaction": timestamp,
        }