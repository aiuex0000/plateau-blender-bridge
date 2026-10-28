"""Tkinter prototype; thin UI over the same CLI/pipeline used for testing."""
from __future__ import annotations
import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from .config import Config
from .pipeline import validate, report, run


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PLATEAU → Blender | MVP 0.1')
        self.geometry('930x800')
        self.minsize(740, 640)
        self.q = queue.Queue()
        self.fields = {}
        self.configure_grid()
        self.after(150, self.poll)

    def configure_grid(self):
        pane = ttk.Frame(self, padding=12)
        pane.pack(fill='both', expand=True)
        pane.columnconfigure(1, weight=1)
        r = 0
        for key, label, mode in (
            ('blender_executable', 'Blender.exe', 'file'),
            ('building_objs', '建物 OBJ (複数可)', 'many'),
            ('terrain_objs', '地形/DEM OBJ (複数可)', 'many'),
            ('aerial_png', '航空写真 PNG', 'file'),
            ('world_file', 'ワールドファイル PGW', 'file'),
            ('output_blend', '出力 .blend', 'save'),
        ):
            ttk.Label(pane, text=label).grid(row=r, column=0, sticky='nw', pady=4)
            var = tk.StringVar()
            self.fields[key] = var
            ttk.Entry(pane, textvariable=var).grid(row=r, column=1, sticky='ew', pady=4)
            ttk.Button(pane, text='参照...', command=lambda k=key, m=mode: self.browse(k,m)).grid(row=r, column=2, padx=(8,0))
            r += 1
        for key, label, value in (
            ('epsg', 'OBJ EPSG（座標の実測に基づき指定）', 'EPSG:6674'),
            ('image_epsg', '画像 EPSG（QGIS出力の設定に基づき指定）', 'EPSG:6674'),
            ('imagery_attribution', '航空写真の出典', '国土地理院（地理院タイル）'),
            ('imagery_terms_url', '利用条件 URL', 'https://maps.gsi.go.jp/help/use.html'),
        ):
            ttk.Label(pane, text=label).grid(row=r, column=0, sticky='w', pady=4)
            var = tk.StringVar(value=value)
            self.fields[key] = var
            ttk.Entry(pane, textvariable=var).grid(row=r, column=1, columnspan=2, sticky='ew', pady=4)
            r += 1
        ttk.Label(pane, text='建物のOBJ軸').grid(row=r, column=0, sticky='w')
        self.baxis = tk.StringVar(value='xyz')
        ttk.Combobox(pane, textvariable=self.baxis, values=['xyz','yxz'], state='readonly', width=9).grid(row=r,column=1,sticky='w')
        r += 1
        ttk.Label(pane, text='地形のOBJ軸').grid(row=r, column=0, sticky='w')
        self.taxis = tk.StringVar(value='xyz')
        ttk.Combobox(pane, textvariable=self.taxis, values=['xyz','yxz'], state='readonly', width=9).grid(row=r,column=1,sticky='w')
        r += 1
        self.allow_partial = tk.BooleanVar(value=False)
        self.allow_suspect = tk.BooleanVar(value=False)
        self.pack_image = tk.BooleanVar(value=True)
        checks = ttk.Frame(pane)
        checks.grid(row=r,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Checkbutton(checks,text='航空写真の一部欠けを許可',variable=self.allow_partial).pack(side='left',padx=4)
        ttk.Checkbutton(checks,text='黒/透明が多い画像を許可',variable=self.allow_suspect).pack(side='left',padx=4)
        ttk.Checkbutton(checks,text='PNGを.blendへ埋め込む',variable=self.pack_image).pack(side='left',padx=4)
        r+=1
        ttk.Label(pane,text='※画像とOBJは同じ投影EPSG・メートルXY座標が必要。EPSGはPNG/PGWから自動判定できない。',foreground='#875600').grid(row=r,column=0,columnspan=3,sticky='w',pady=5)
        r+=1
        bar = ttk.Frame(pane)
        bar.grid(row=r,column=0,columnspan=3,sticky='ew',pady=6)
        self.preflight_button = ttk.Button(bar,text='事前チェック',command=self.preflight)
        self.preflight_button.pack(side='left',padx=4)
        self.run_button = ttk.Button(bar,text='生成 (.blend)',command=self.generate)
        self.run_button.pack(side='left',padx=4)
        ttk.Button(bar,text='設定を保存',command=self.save_config).pack(side='left',padx=4)
        ttk.Button(bar,text='設定を開く',command=self.open_config).pack(side='left',padx=4)
        r += 1
        ttk.Label(pane,text='実行ログ').grid(row=r,column=0,sticky='w')
        r += 1
        frame = ttk.Frame(pane)
        frame.grid(row=r,column=0,columnspan=3,sticky='nsew')
        pane.rowconfigure(r,weight=1)
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        self.logbox = tk.Text(frame,wrap='word',height=12)
        self.logbox.grid(row=0,column=0,sticky='nsew')
        sb = ttk.Scrollbar(frame,command=self.logbox.yview)
        sb.grid(row=0,column=1,sticky='ns')
        self.logbox.configure(yscrollcommand=sb.set)

    def browse(self,key,mode):
        if mode == 'many':
            files = filedialog.askopenfilenames(title=key,filetypes=[('OBJ files','*.obj')])
            if files:
                self.fields[key].set(' | '.join(files))
        elif mode == 'save':
            path = filedialog.asksaveasfilename(defaultextension='.blend',filetypes=[('Blender','*.blend')])
            if path:
                self.fields[key].set(path)
        else:
            filters = [('All files','*.*')]
            if key == 'aerial_png': filters=[('PNG','*.png')]
            if key == 'world_file': filters=[('World files','*.pgw *.wld *.pngw')]
            if key == 'blender_executable': filters=[('Blender executable','*.exe'),('All files','*.*')]
            path = filedialog.askopenfilename(filetypes=filters)
            if path:
                self.fields[key].set(path)

    def gather(self):
        required = ['blender_executable','building_objs','terrain_objs','aerial_png','world_file','output_blend','epsg','image_epsg','imagery_attribution','imagery_terms_url']
        data = {k:self.fields[k].get().strip() for k in required}
        data['building_objs'] = [s.strip() for s in data['building_objs'].split(' | ') if s.strip()]
        data['terrain_objs'] = [s.strip() for s in data['terrain_objs'].split(' | ') if s.strip()]
        data.update(building_axis=self.baxis.get(),terrain_axis=self.taxis.get(),
                    allow_partial_imagery=self.allow_partial.get(),
                    allow_suspect_imagery=self.allow_suspect.get(),pack_image=self.pack_image.get())
        # Absolute paths from the file browser avoid ambiguity across save locations.
        return data

    def _write_temp_config(self):
        # Configuration remains inspectable, in the user's output work folder.
        data = self.gather()
        if not data['output_blend']:
            raise ValueError('出力.blendを指定してください')
        path = Path(data['output_blend']).expanduser().resolve().with_suffix('.work') / 'gui_config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return Config.load(path)

    def save_config(self):
        path = filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            Path(path).write_text(json.dumps(self.gather(),ensure_ascii=False,indent=2),encoding='utf-8')
            self.message('[CONFIG] ' + path)

    def open_config(self):
        path = filedialog.askopenfilename(filetypes=[('JSON','*.json')])
        if not path: return
        try:
            cfg = Config.load(Path(path))
            for k in ('blender_executable','aerial_png','world_file','output_blend'):
                self.fields[k].set(str(getattr(cfg,k)))
            for k in ('building_objs','terrain_objs'):
                self.fields[k].set(' | '.join(str(p) for p in getattr(cfg,k)))
            for k in ('epsg','image_epsg','imagery_attribution','imagery_terms_url'):
                self.fields[k].set(str(getattr(cfg,k)))
            self.baxis.set(cfg.building_axis); self.taxis.set(cfg.terrain_axis)
            self.allow_partial.set(cfg.allow_partial_imagery)
            self.allow_suspect.set(cfg.allow_suspect_imagery)
            self.pack_image.set(cfg.pack_image)
        except Exception as exc:
            messagebox.showerror('設定エラー',str(exc))

    def message(self,msg):
        self.logbox.insert('end',str(msg)+'\n')
        self.logbox.see('end')

    def start(self, job):
        self.run_button.configure(state='disabled')
        self.preflight_button.configure(state='disabled')
        def worker():
            try:
                job()
                self.q.put(('done','完了'))
            except Exception as exc:
                self.q.put(('error',f'{type(exc).__name__}: {exc}'))
        threading.Thread(target=worker,daemon=True).start()

    def preflight(self):
        try: cfg = self._write_temp_config()
        except Exception as exc:
            messagebox.showerror('設定エラー',str(exc)); return
        self.start(lambda: self.q.put(('log',json.dumps(report(validate(cfg)),ensure_ascii=False,indent=2))))

    def generate(self):
        try: cfg = self._write_temp_config()
        except Exception as exc:
            messagebox.showerror('設定エラー',str(exc)); return
        overwrite = False
        if cfg.output_blend.exists():
            overwrite = messagebox.askyesno('既存ファイルあり','既存 .blend を上書きしますか？\n元の .blend は戻せません。')
            if not overwrite: return
        self.start(lambda: run(cfg,overwrite_output=overwrite,log=lambda line:self.q.put(('log',str(line)))))

    def poll(self):
        try:
            while True:
                kind,payload=self.q.get_nowait()
                if kind=='log': self.message(payload)
                else:
                    self.run_button.configure(state='normal')
                    self.preflight_button.configure(state='normal')
                    self.message('['+kind.upper()+'] '+payload)
                    if kind=='error': messagebox.showerror('処理失敗',payload)
                    elif kind=='done': messagebox.showinfo('処理完了',payload)
        except queue.Empty:
            pass
        self.after(150,self.poll)


def main():
    from .runtime import enable_dpi_awareness
    enable_dpi_awareness()
    App().mainloop()


if __name__=='__main__':
    main()
