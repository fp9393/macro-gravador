"""
Gravador de Macro de Mouse (Windows)
------------------------------------
Grava movimentos e cliques do mouse e reproduz em loop (infinito ou N vezes),
respeitando o tempo gravado (incluindo as pausas e o tempo até você parar a gravação).

Não precisa instalar nada além do Python 3 para Windows.
Para rodar:  python macro_gravador.py

Atalhos globais (funcionam mesmo com o app minimizado):
  F8  -> iniciar / parar gravação
  F9  -> iniciar / parar reprodução
  ESC -> parada de emergência (para gravação e reprodução)
"""

import ctypes
import json
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if not hasattr(ctypes, "windll"):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Sistema não suportado", "Este app funciona somente no Windows.")
    sys.exit(1)

user32 = ctypes.windll.user32

# Faz as coordenadas serem reais (sem distorção por escala de tela)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


VK_F8, VK_F9, VK_ESC = 0x77, 0x78, 0x1B

# botão -> (tecla virtual, flag down, flag up)
BOTOES = {
    "esquerdo": (0x01, 0x0002, 0x0004),
    "direito": (0x02, 0x0008, 0x0010),
    "meio": (0x04, 0x0020, 0x0040),
}


def pos_mouse():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def tecla_pressionada(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def mover(x, y):
    user32.SetCursorPos(int(x), int(y))


def botao(nome, pressionar):
    _, down, up = BOTOES[nome]
    user32.mouse_event(down if pressionar else up, 0, 0, 0, 0)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Gravador de Macro de Mouse")
        root.geometry("420x400")
        root.resizable(False, False)

        self.eventos = []
        self.duracao = 0.0

        self.gravando = False
        self.reproduzindo = False
        self.parar_gravacao = threading.Event()
        self.parar_reproducao = threading.Event()
        self.voltas = 0
        self.fim_reproducao_pendente = False

        self.infinito = tk.BooleanVar(value=True)
        self.repeticoes = tk.IntVar(value=1)
        self.velocidade = tk.DoubleVar(value=1.0)
        self.pausa = tk.DoubleVar(value=0.0)
        self.status = tk.StringVar(value="Pronto. Aperte F8 para gravar.")
        self.info = tk.StringVar(value="Nenhuma gravação.")

        self._montar_interface()

        self._tecla_anterior = {VK_F8: False, VK_F9: False, VK_ESC: False}
        self._loop_ui()

    # ---------------- Interface ----------------
    def _montar_interface(self):
        pad = {"padx": 12, "pady": 6}

        ttk.Label(self.root, textvariable=self.status, font=("Segoe UI", 11, "bold"),
                  wraplength=390).pack(**pad)
        ttk.Label(self.root, textvariable=self.info).pack(padx=12)

        f1 = ttk.Frame(self.root)
        f1.pack(fill="x", **pad)
        self.btn_gravar = ttk.Button(f1, text="● Gravar (F8)", command=self.alternar_gravacao)
        self.btn_gravar.pack(side="left", expand=True, fill="x", padx=4)
        self.btn_tocar = ttk.Button(f1, text="▶ Reproduzir (F9)", command=self.alternar_reproducao)
        self.btn_tocar.pack(side="left", expand=True, fill="x", padx=4)

        box = ttk.LabelFrame(self.root, text="Loop")
        box.pack(fill="x", **pad)

        ttk.Checkbutton(box, text="Loop infinito (até apertar F9 ou ESC)",
                        variable=self.infinito, command=self._atualiza_campos).pack(anchor="w", padx=8, pady=4)

        l1 = ttk.Frame(box)
        l1.pack(fill="x", padx=8, pady=2)
        ttk.Label(l1, text="Repetições (se não for infinito):").pack(side="left")
        self.spin_rep = ttk.Spinbox(l1, from_=1, to=999999, width=8, textvariable=self.repeticoes)
        self.spin_rep.pack(side="right")

        l2 = ttk.Frame(box)
        l2.pack(fill="x", padx=8, pady=2)
        ttk.Label(l2, text="Velocidade (1.0 = tempo gravado):").pack(side="left")
        ttk.Spinbox(l2, from_=0.1, to=10, increment=0.1, width=8, textvariable=self.velocidade).pack(side="right")

        l3 = ttk.Frame(box)
        l3.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(l3, text="Pausa extra entre voltas (s):").pack(side="left")
        ttk.Spinbox(l3, from_=0, to=3600, increment=0.5, width=8, textvariable=self.pausa).pack(side="right")

        f2 = ttk.Frame(self.root)
        f2.pack(fill="x", **pad)
        ttk.Button(f2, text="Salvar macro...", command=self.salvar).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(f2, text="Abrir macro...", command=self.abrir).pack(side="left", expand=True, fill="x", padx=4)

        ttk.Label(
            self.root,
            text="F8 grava/para • F9 toca/para • ESC = parada de emergência\n"
                 "O app minimiza sozinho ao gravar/reproduzir.",
            justify="center", foreground="#555",
        ).pack(padx=12, pady=6)

        self._atualiza_campos()

    def _atualiza_campos(self):
        self.spin_rep.configure(state="disabled" if self.infinito.get() else "normal")

    # ---------------- Gravação ----------------
    def alternar_gravacao(self):
        if self.reproduzindo:
            return
        if self.gravando:
            self.parar_gravacao.set()
        else:
            self._iniciar_gravacao()

    def _iniciar_gravacao(self):
        self.gravando = True
        self.parar_gravacao.clear()
        self.status.set("Gravando... (F8 para parar)")
        self.btn_gravar.configure(text="■ Parar (F8)")
        self.root.iconify()
        threading.Thread(target=self._thread_gravacao, daemon=True).start()

    def _thread_gravacao(self):
        eventos = []
        inicio = time.perf_counter()
        ultimo = pos_mouse()
        eventos.append({"t": 0.0, "tipo": "mover", "x": ultimo[0], "y": ultimo[1]})
        estado = {b: False for b in BOTOES}

        while not self.parar_gravacao.is_set():
            t = time.perf_counter() - inicio
            x, y = pos_mouse()
            if (x, y) != ultimo:
                eventos.append({"t": round(t, 4), "tipo": "mover", "x": x, "y": y})
                ultimo = (x, y)
            for nome, (vk, _, _) in BOTOES.items():
                p = tecla_pressionada(vk)
                if p != estado[nome]:
                    estado[nome] = p
                    eventos.append({"t": round(t, 4), "tipo": "baixo" if p else "cima",
                                    "botao": nome, "x": x, "y": y})
            time.sleep(0.004)

        duracao = time.perf_counter() - inicio
        # solta botões que ficaram pressionados ao parar
        for nome, p in estado.items():
            if p:
                eventos.append({"t": round(duracao, 4), "tipo": "cima", "botao": nome,
                                "x": ultimo[0], "y": ultimo[1]})
        self.eventos = eventos
        self.duracao = duracao
        self.gravando = False
        self.fim_gravacao_pendente = True

    # ---------------- Reprodução ----------------
    def alternar_reproducao(self):
        if self.gravando:
            return
        if self.reproduzindo:
            self.parar_reproducao.set()
            return
        if not self.eventos:
            messagebox.showinfo("Sem gravação", "Grave ou abra uma macro primeiro.")
            return
        try:
            vel = float(self.velocidade.get())
            pausa = float(self.pausa.get())
            reps = int(self.repeticoes.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Valor inválido", "Confira velocidade, pausa e repetições.")
            return
        if vel <= 0:
            messagebox.showerror("Valor inválido", "A velocidade deve ser maior que zero.")
            return

        self.reproduzindo = True
        self.parar_reproducao.clear()
        self.voltas = 0
        self.btn_tocar.configure(text="■ Parar (F9)")
        self.root.iconify()
        threading.Thread(
            target=self._thread_reproducao,
            args=(vel, max(0.0, pausa), self.infinito.get(), max(1, reps)),
            daemon=True,
        ).start()

    def _esperar(self, alvo_abs):
        """Espera até o instante alvo (perf_counter). Retorna True se foi interrompido."""
        while True:
            if self.parar_reproducao.is_set():
                return True
            resto = alvo_abs - time.perf_counter()
            if resto <= 0:
                return False
            if resto > 0.02:
                self.parar_reproducao.wait(resto - 0.015)
            # nos últimos ms, gira rápido para ter precisão

    def _thread_reproducao(self, vel, pausa, infinito, reps):
        apertados = set()
        time.sleep(0.6)  # tempo para a janela minimizar
        try:
            while not self.parar_reproducao.is_set():
                inicio = time.perf_counter()
                interrompido = False
                for ev in self.eventos:
                    if self._esperar(inicio + ev["t"] / vel):
                        interrompido = True
                        break
                    if ev["tipo"] == "mover":
                        mover(ev["x"], ev["y"])
                    elif ev["tipo"] == "baixo":
                        mover(ev["x"], ev["y"])
                        botao(ev["botao"], True)
                        apertados.add(ev["botao"])
                    elif ev["tipo"] == "cima":
                        mover(ev["x"], ev["y"])
                        botao(ev["botao"], False)
                        apertados.discard(ev["botao"])
                if interrompido:
                    break
                # completa o tempo total gravado (inclui a espera final antes de você parar de gravar)
                if self._esperar(inicio + self.duracao / vel):
                    break
                self.voltas += 1
                if not infinito and self.voltas >= reps:
                    break
                if pausa > 0 and self._esperar(time.perf_counter() + pausa):
                    break
        finally:
            for b in list(apertados):
                botao(b, False)
            self.reproduzindo = False
            self.fim_reproducao_pendente = True

    # ---------------- Arquivos ----------------
    def salvar(self):
        if not self.eventos:
            messagebox.showinfo("Sem gravação", "Nada para salvar ainda.")
            return
        caminho = filedialog.asksaveasfilename(defaultextension=".json",
                                               filetypes=[("Macro (JSON)", "*.json")])
        if caminho:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump({"duracao": self.duracao, "eventos": self.eventos}, f)
            self.status.set("Macro salva.")

    def abrir(self):
        caminho = filedialog.askopenfilename(filetypes=[("Macro (JSON)", "*.json")])
        if not caminho:
            return
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
            self.eventos = dados["eventos"]
            self.duracao = float(dados["duracao"])
        except Exception as e:
            messagebox.showerror("Erro ao abrir", f"Arquivo inválido: {e}")
            return
        self._atualiza_info()
        self.status.set("Macro carregada. Aperte F9 para reproduzir.")

    def _atualiza_info(self):
        if self.eventos:
            self.info.set(f"{len(self.eventos)} eventos • duração de {self.duracao:.1f} s")
        else:
            self.info.set("Nenhuma gravação.")

    # ---------------- Loop de interface / atalhos globais ----------------
    def _borda(self, vk):
        atual = tecla_pressionada(vk)
        subiu = atual and not self._tecla_anterior[vk]
        self._tecla_anterior[vk] = atual
        return subiu

    def _loop_ui(self):
        if self._borda(VK_ESC):
            self.parar_gravacao.set()
            self.parar_reproducao.set()
        if self._borda(VK_F8):
            self.alternar_gravacao()
        if self._borda(VK_F9):
            self.alternar_reproducao()

        if getattr(self, "fim_gravacao_pendente", False):
            self.fim_gravacao_pendente = False
            self.btn_gravar.configure(text="● Gravar (F8)")
            self._atualiza_info()
            self.status.set("Gravação concluída. Aperte F9 para reproduzir.")
            self.root.deiconify()

        if self.fim_reproducao_pendente:
            self.fim_reproducao_pendente = False
            self.btn_tocar.configure(text="▶ Reproduzir (F9)")
            self.status.set(f"Reprodução parada após {self.voltas} volta(s) completa(s).")
            self.root.deiconify()

        if self.reproduzindo:
            self.status.set(f"Reproduzindo... volta {self.voltas + 1} (F9 ou ESC para parar)")

        self.root.after(30, self._loop_ui)


if __name__ == "__main__":
    janela = tk.Tk()
    App(janela)
    janela.mainloop()
