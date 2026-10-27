"""User-facing automatic acquisition UI. Tkinter only, pipeline in a worker thread."""
from __future__ import annotations
import dataclasses
import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from .auto import AutoRequest, preview, generate
from .discovery import blender_candidates, find_nusamai
from .runtime import CancelToken, CancelledError
from .diagnostics import inspect_environment
from .resources import forecast, enforce_budget
from .auto import projected_selection, center_condition
from .smoke import run_smoke
from .catalog import NoPlateauData

CENTER_LABEL='中心の 緯度, 経度（, 一辺m）'
CENTER_HINT='例: 35.6896, 139.6917 （Googleマップで場所を右クリック→数字をクリックでコピー。一辺は下の選択を使用。矩形の4つの数字もOK）'
PLACE_PRESETS=(
    ('東京タワー','35.6586, 139.7454','400'),
    ('東京スカイツリー','35.7101, 139.8107','400'),
    ('新宿駅西口','35.6905, 139.6995','200'),
    ('渋谷スクランブル交差点','35.6595, 139.7005','200'),
    ('東京駅 丸の内','35.6812, 139.7671','400'),
    ('富士山 山頂','35.3606, 138.7274','800'),
    ('横浜 みなとみらい','35.4575, 139.6320','400'),
    ('大阪城','34.6873, 135.5262','400'),
    ('京都駅','34.9858, 135.7588','400'),
    ('広島 原爆ドーム','34.3955, 132.4536','400'),
)
STAGES=(
    (('[CATALOG]','[PLAN]','[CRS]'),'データを探しています'),
    (('[CONVERTER]','[SUBSET]','[CLIP]','[DEDUP]','[AXIS]','[BBOX]','[TEXTURE'),'建物を準備しています'),
    (('[GSI DEM]','[GSI PHOTO]'),'地形と写真を準備しています'),
    (('[BLENDER]','[OBJ]','[VERIFY]','[CHECK]'),'Blenderで保存・確認しています'),
)


def stage_for(line:str,current:int)->int:
    for stage,(prefixes,_) in enumerate(STAGES,1):
        if line.startswith(prefixes):return max(current,stage)
    return current


def free_output_path(path:Path)->Path:
    """Never overwrite: pick scene_2.blend, scene_3.blend ... next to an existing file."""
    if not path.exists():return path
    n=2
    while (candidate:=path.with_name(f'{path.stem}_{n}{path.suffix}')).exists():n+=1
    return candidate


def user_error(exc:Exception)->str:
    if isinstance(exc,NoPlateauData):return str(exc)
    return f'{type(exc).__name__}: {exc}'


class AutoGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PLATEAU → Blender | 自動生成 v0.10')
        self.geometry('900x780')
        self.minsize(730,580)
        self.queue=queue.Queue()
        self.vars={}
        self.running=False
        self.generate_stage=0
        self.cancel_token=None
        self.last_result:Path|None=None
        self.create_widgets()
        self.after(100,self.poll)
        self.after(250,self.scan_dependencies)

    def create_widgets(self):
        canvas=tk.Canvas(self,highlightthickness=0)
        scrollbar=ttk.Scrollbar(self,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',expand=True,fill='both')
        canvas.configure(yscrollcommand=scrollbar.set)
        container=ttk.Frame(canvas,padding=12)
        window=canvas.create_window((0,0),window=container,anchor='nw')
        container.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
        container.columnconfigure(0,weight=1)
        ttk.Label(container,text='好きな街を3Dにしよう',font=('Yu Gothic UI',16,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(container,text='場所と広さを選んで、Blenderで開ける街を作ります。').grid(row=1,column=0,sticky='w',pady=(0,8))
        place=ttk.LabelFrame(container,text='① 場所を選ぶ',padding=8)
        place.grid(row=2,column=0,sticky='ew');place.columnconfigure(0,weight=1)
        ttk.Button(place,text='Googleマップで場所を探す',command=lambda:webbrowser.open('https://www.google.com/maps')).grid(row=0,column=0,sticky='w')
        ttk.Label(place,text='地図を右クリック → 座標の数字をクリックしてコピー → 下に貼り付け',wraplength=700).grid(row=1,column=0,sticky='w')
        self.mode=tk.StringVar(value='center')
        ttk.Label(place,text='有名な場所から選ぶ').grid(row=2,column=0,sticky='w')
        self.preset=ttk.Combobox(place,values=tuple(p[0] for p in PLACE_PRESETS),state='readonly')
        self.preset.grid(row=3,column=0,sticky='ew')
        self.preset.bind('<<ComboboxSelected>>',self.apply_preset)
        self.applying_preset=False
        modes=ttk.Frame(place);modes.grid(row=4,column=0,sticky='w')
        for label,value in [('中心点','center'),('矩形','bbox'),('メッシュコード','mesh')]:
            ttk.Radiobutton(modes,text=label,variable=self.mode,value=value,command=self.update_area).pack(side='left',padx=4)
        self.area_label=ttk.Label(place,text=CENTER_LABEL);self.area_label.grid(row=5,column=0,sticky='w')
        self.area=tk.StringVar(value='')
        self.area_entry=ttk.Entry(place,textvariable=self.area);self.area_entry.grid(row=6,column=0,sticky='ew')
        self.check_button=ttk.Button(place,text='この場所を確認',command=self.check_place);self.check_button.grid(row=6,column=1,padx=5)
        self.hint=ttk.Label(place,text=CENTER_HINT,wraplength=700);self.hint.grid(row=7,column=0,columnspan=2,sticky='w')
        self.place_info=tk.StringVar(value='')
        ttk.Label(place,textvariable=self.place_info,wraplength=700).grid(row=8,column=0,columnspan=2,sticky='w')
        self.area.trace_add('write',self.area_changed)
        size=ttk.LabelFrame(container,text='② 広さを選ぶ（中心点の場合）',padding=8);size.grid(row=3,column=0,sticky='ew',pady=5)
        self.size=tk.StringVar(value='400')
        for label,value in [('小さめ 200m','200'),('標準 400m','400'),('広め 800m','800'),('入力欄の指定を使う','input')]:
            ttk.Radiobutton(size,text=label,variable=self.size,value=value).pack(side='left',padx=4)
        save=ttk.LabelFrame(container,text='③ 保存先',padding=8);save.grid(row=4,column=0,sticky='ew');save.columnconfigure(0,weight=1)
        self.blender_var=tk.StringVar();self.nusamai_var=tk.StringVar()
        self.output_var=tk.StringVar(value=str(Path.home()/'Documents'/'PLATEAU'/'scene.blend'))
        ttk.Entry(save,textvariable=self.output_var).grid(row=0,column=0,sticky='ew')
        ttk.Button(save,text='保存先を変更',command=lambda:self.choose(self.output_var,'save')).grid(row=0,column=1,padx=5)
        actions=ttk.Frame(container);actions.grid(row=5,column=0,sticky='ew',pady=8)
        self.generate_button=ttk.Button(actions,text='3Dの街を作る',command=self.start_generate);self.generate_button.pack(side='left',expand=True,fill='x')
        self.cancel_button=ttk.Button(actions,text='中止',command=self.cancel_running,state='disabled');self.cancel_button.pack(side='left',padx=4)
        self.open_button=ttk.Button(actions,text='Blenderで街を開く',command=self.open_result,state='disabled');self.open_button.pack(side='left')
        self.status=tk.StringVar(value='環境スキャン中…')
        self.status_label=ttk.Label(container,textvariable=self.status,wraplength=700);self.status_label.grid(row=6,column=0,sticky='w')
        self.progress=ttk.Progressbar(container,mode='determinate',value=0);self.progress.grid(row=7,column=0,sticky='ew')
        self.advanced=ttk.Frame(container,padding=8)
        self.advanced.columnconfigure(1,weight=1)
        self.advanced_visible=False
        self.advanced_button=ttk.Button(container,text='詳細設定・診断を開く ▾',command=self.toggle_advanced)
        self.advanced_button.grid(row=8,column=0,sticky='w',pady=4)
        row=0
        for label,var,kind in [('Blender本体',self.blender_var,'blender'),('nusamai CLI',self.nusamai_var,'nusamai')]:
            ttk.Label(self.advanced,text=label).grid(row=row,column=0,sticky='w')
            ttk.Entry(self.advanced,textvariable=var).grid(row=row,column=1,sticky='ew')
            ttk.Button(self.advanced,text='参照',command=lambda v=var,k=kind:self.choose(v,k)).grid(row=row,column=2);row+=1
        self.scan_button=ttk.Button(self.advanced,text='Blender / CLIを再スキャン',command=self.scan_dependencies);self.scan_button.grid(row=row,column=1,sticky='w');row+=1
        self.epsg=tk.StringVar(value='AUTO');self.lod=tk.StringVar(value='textured_max_lod');self.zoom=tk.StringVar(value='18');self.dem_res=tk.StringVar(value='100');self.axis=tk.StringVar(value='auto')
        for label,var,values in [('EPSG',self.epsg,None),('建物LOD',self.lod,('min_lod','max_lod','textured_max_lod')),('航空写真ズーム',self.zoom,('14','15','16','17','18')),('地形のサンプル密度',self.dem_res,('40','60','100','160','256')),('OBJ座標軸',self.axis,('auto','xyz','yxz'))]:
            ttk.Label(self.advanced,text=label).grid(row=row,column=0,sticky='w')
            control=ttk.Combobox(self.advanced,textvariable=var,values=values,state='readonly') if values else ttk.Entry(self.advanced,textvariable=var)
            control.grid(row=row,column=1,columnspan=2,sticky='ew');row+=1
        self.clip=tk.BooleanVar(value=True)
        ttk.Checkbutton(self.advanced,text='矩形範囲で建物を切り出す',variable=self.clip).grid(row=row,column=0,columnspan=3,sticky='w');row+=1
        diagnostic=ttk.Frame(self.advanced);diagnostic.grid(row=row,column=0,columnspan=3,sticky='w');row+=1
        self.preview_button=ttk.Button(diagnostic,text='取得対象の確認',command=self.preview_sources);self.preview_button.pack(side='left')
        for label,command in [('環境診断',self.diagnose),('Blender自己診断',self.smoke_test),('手動OBJモード',self.open_legacy)]:
            ttk.Button(diagnostic,text=label,command=command).pack(side='left',padx=3)
        self.log=tk.Text(self.advanced,height=6,wrap='word');self.log.grid(row=row,column=0,columnspan=3,sticky='ew');row+=1
        ttk.Label(container,text='データ出典: Project PLATEAU / 国土地理院。公開時は利用条件をご確認ください。',wraplength=700).grid(row=10,column=0,sticky='w')

    def area_changed(self,*_):
        self.place_info.set('')
        if not self.applying_preset:self.preset.set('')

    def apply_preset(self,event=None):
        index=self.preset.current()
        if index<0:return
        _,coordinates,size=PLACE_PRESETS[index]
        self.applying_preset=True
        try:
            self.mode.set('center')
            self.update_area()
            self.area.set(coordinates)
            self.size.set(size)
            self.place_info.set('')
        finally:
            self.applying_preset=False

    def toggle_advanced(self):
        self.advanced_visible=not self.advanced_visible
        if self.advanced_visible:
            self.advanced.grid(row=9,column=0,sticky='ew')
            self.advanced_button.configure(text='詳細設定・診断を閉じる ▴')
        else:
            self.advanced.grid_remove()
            self.advanced_button.configure(text='詳細設定・診断を開く ▾')

    def update_area(self):
        mode=self.mode.get()
        labels={'center':(CENTER_LABEL,CENTER_HINT),
                'bbox':('範囲 west,south,east,north','例: 135.42,34.81,135.43,34.82（経度・緯度。小範囲から試す）'),
                'mesh':('メッシュコード','例: 5235xxxx（6/8/9/10桁のメッシュ番号）')}
        title,hint=labels[mode];self.area_label.configure(text=title);self.hint.configure(text=hint)
        self.area.set('')

    def choose(self,var,kind):
        if kind=='save':
            filename=filedialog.asksaveasfilename(filetypes=[('Blender','*.blend')],defaultextension='.blend')
        else:
            filename=filedialog.askopenfilename(filetypes=[('Windows executable','*.exe'),('Any file','*.*')])
        if filename:var.set(filename)

    def build_request(self)->AutoRequest:
        raw=self.area.get().strip()
        if not raw:raise ValueError('地域または範囲を指定してください')
        mode=self.mode.get()
        if mode=='bbox':condition='r:'+raw.replace(' ','')
        elif mode=='mesh':condition='m:'+raw.replace(' ','')
        else:
            notes=[]
            condition=center_condition(raw, default_size_m=float(self.size.get()) if self.size.get()!='input' else 400.0, notes=notes)
            if notes:self.place_info.set('↔ '+notes[0])
        return AutoRequest(condition=condition,epsg=self.epsg.get().strip(),
            output_blend=Path(self.output_var.get()).expanduser().resolve(),
            blender_executable=Path(self.blender_var.get()) if self.blender_var.get() else None,
            nusamai_executable=Path(self.nusamai_var.get()) if self.nusamai_var.get() else None,
            lod=self.lod.get(),photo_zoom=int(self.zoom.get()),terrain_resolution=int(self.dem_res.get()),
            axis_override=self.axis.get(),clip_requested_area=self.clip.get())

    def queue_msg(self,s):self.queue.put(('log',str(s)))
    def _run_worker(self,method,generate_job=False):
        if self.running:return
        try:request=self.build_request();request.check()
        except Exception as exc:messagebox.showerror('入力エラー',str(exc));return
        self.running=True
        self.generate_stage=1 if generate_job else 0
        if self.generate_stage:self.status.set(f'(1/4) {STAGES[0][1]}…')
        self.cancel_token=CancelToken()
        self.preview_button.configure(state='disabled');self.generate_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.configure(mode='indeterminate');self.progress.start(14)
        def worker():
            try:method(request)
            except Exception as exc:self.queue.put(('error',user_error(exc)))
            else:self.queue.put(('done','完了'))
        threading.Thread(target=worker,daemon=True).start()

    def preview_sources(self):
        def job(req):
            sources=preview(req,cancel=self.cancel_token)
            self.queue_msg(f'[CRS] 予定EPSG: {req.effective_epsg(sources)}')
            self.queue_msg(f'PLATEAU検索: {len(sources)}ファイル / 判明サイズ: {sum(s.size or 0 for s in sources)/1048576:.1f} MiB')
            for s in sources:self.queue_msg(f'  {s.city} 年度{s.year} / {s.mesh} / LOD{s.max_lod} / {(s.size or 0)/1048576:.1f}MiB')
            if req.condition.startswith('r:'):
                selected=projected_selection(req.condition,req.effective_epsg(sources))
                if selected:
                    plan=forecast(selected,req.effective_epsg(sources),wanted_zoom=req.photo_zoom,dem_resolution=req.terrain_resolution)
                    self.queue_msg(f'[取得計画] 写真z{plan.photo_zoom} {plan.photo_tiles}タイル、{plan.photo_width}x{plan.photo_height}px、DEM {plan.dem_tile_positions}タイル位置(最大{plan.dem_upper_bound_requests}試行)、頂点約{plan.terrain_vertices}')
                    enforce_budget(plan)
            self.queue_msg('※ APIの取得単位はGMLファイル。生成時には矩形を投影XYの近似矩形へ切り出します。')
        self._run_worker(job)

    def check_place(self):
        """Catalog-only lookup (no GML download) so users learn early whether the place has data."""
        if self.running:return
        try:request=self.build_request()
        except Exception as exc:self.place_info.set('✗ '+str(exc));return
        self.check_button.configure(state='disabled')
        swap=self.place_info.get()
        self.place_info.set((swap+'\n' if swap else '')+'PLATEAUのデータを探しています…')
        def worker():
            try:
                sources=preview(request)
                cities='、'.join(dict.fromkeys(f'{s.city}（{s.year}年度）' for s in sources))
                size=sum(s.size or 0 for s in sources)/1048576
                text=f'✓ {cities}のデータがあります・GML {len(sources)}件・約{size:.0f}MB'
            except Exception as exc:
                text='✗ '+(str(exc) if isinstance(exc,ValueError) else user_error(exc))
            self.queue.put(('place',(swap+'\n' if swap else '')+text))
        threading.Thread(target=worker,daemon=True).start()

    def start_generate(self):
        try:request=self.build_request()
        except Exception as exc:messagebox.showerror('入力エラー',str(exc));return
        renamed=''
        if request.output_blend.exists():
            fresh=free_output_path(request.output_blend)
            self.output_var.set(str(fresh));request=dataclasses.replace(request,output_blend=fresh)
            renamed='\n（同じ名前のファイルがあるので、上書きせず新しい名前で保存します）'
        if not messagebox.askyesno('作成内容の確認',f'場所: {self.area.get()}\n広さ: {self.size.get()}（入力欄に広さ・矩形がある場合はそちらを優先）\n保存先: {request.output_blend}{renamed}\n\n建物・地形・航空写真を取得して作成します。よろしいですか？'):
            return
        self.last_result=None;self.open_button.configure(state='disabled',text='Blenderで街を開く')
        def job(req):
            result=generate(req,log=self.queue_msg,cancel=self.cancel_token)
            self.queue.put(('result',str(result)))
        self._run_worker(job,generate_job=True)

    def cancel_running(self):
        if self.running and self.cancel_token:
            self.cancel_token.cancel()
            self.cancel_button.configure(state='disabled')
            self.queue_msg('[STOP] 中断要求を受け付けました。通信・子プロセスを終了中')

    def diagnose(self):
        if self.running:return
        blender=Path(self.blender_var.get()) if self.blender_var.get() else None
        nusamai=Path(self.nusamai_var.get()) if self.nusamai_var.get() else None
        output=Path(self.output_var.get())
        self.queue_msg('[DIAGNOSTICS] Blender/nusamai の --version を確認中')
        def worker():
            try:
                report=inspect_environment(blender=blender,nusamai=nusamai,
                                            output=output,check_versions=True)
                self.queue_msg('[DIAGNOSTICS] '+json.dumps(report,ensure_ascii=False))
            except Exception as exc:self.queue_msg('[DIAGNOSTICS ERROR] '+str(exc))
        threading.Thread(target=worker,daemon=True).start()

    def smoke_test(self):
        if self.running:return
        binary=Path(self.blender_var.get()) if self.blender_var.get() else None
        self.queue_msg('[SMOKE] 合成データによるBlender保存・再読込を検証')
        self.running=True
        self.cancel_token=CancelToken()
        self.preview_button.configure(state='disabled');self.generate_button.configure(state='disabled')
        self.cancel_button.configure(state='normal');self.progress.configure(mode='indeterminate');self.progress.start(14)
        def worker():
            try:
                output=run_smoke(blender=binary,output_parent=Path(self.output_var.get()).expanduser().resolve().parent/'diagnostics',log=self.queue_msg,cancel=self.cancel_token)
                self.queue.put(('smoke_result',str(output)))
            except Exception as exc:self.queue.put(('error',f'{type(exc).__name__}: {exc}'))
            else:self.queue.put(('done','Blender自己診断成功。検証用 .blend を開いて確認できるよ。'))
        threading.Thread(target=worker,daemon=True).start()

    def scan_dependencies(self):
        self.status.set('Blender / nusamai 実行ファイルを検索中…')
        def task():
            try:
                blender=blender_candidates()
                nusamai=find_nusamai()
                self.queue.put(('scan',(blender,nusamai)))
            except Exception as exc:self.queue.put(('error',f'検索エラー: {exc}'))
        threading.Thread(target=task,daemon=True).start()

    def open_result(self):
        if not self.last_result:return
        blender=Path(self.blender_var.get())
        try:subprocess.Popen([str(blender),str(self.last_result)])
        except OSError as exc:messagebox.showerror('起動失敗',str(exc))

    def open_legacy(self):
        if getattr(sys,'frozen',False):
            subprocess.Popen([sys.executable,'--manual'])
        else:
            subprocess.Popen([sys.executable,'-m','plateau_blend.gui'])

    def poll(self):
        try:
            while True:
                kind,data=self.queue.get_nowait()
                if kind=='scan':
                    blender,nusamai=data
                    if blender and not self.blender_var.get():self.blender_var.set(str(blender[0]))
                    if nusamai and not self.nusamai_var.get():self.nusamai_var.set(str(nusamai))
                    if not self.generate_stage:self.status.set('準備OK' if blender and nusamai else '必要なソフトが見つかりません。詳細設定で確認してください。')
                elif kind=='place':
                    self.place_info.set(data);self.check_button.configure(state='normal')
                elif kind=='log':
                    self.log.insert('end',data+'\n');self.log.see('end')
                    if self.generate_stage:
                        self.generate_stage=stage_for(data,self.generate_stage)
                        self.status.set(f'({self.generate_stage}/4) {STAGES[self.generate_stage-1][1]}…')
                elif kind in ('result','smoke_result'):
                    self.last_result=Path(data)
                    self.open_button.configure(state='normal',text='Blenderで街を開く' if kind=='result' else '診断ファイルを開く')
                else:
                    self.running=False;self.generate_stage=0;self.progress.stop()
                    self.progress.configure(mode='determinate',value=0)
                    self.preview_button.configure(state='normal');self.generate_button.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                    self.log.insert('end',f'[{kind.upper()}] {data}\n');self.log.see('end')
                    if kind=='error':
                        self.status.set(data if data.startswith('この場所には') else '処理に失敗しました。詳細設定のログを確認してください。')
                        messagebox.showerror('処理失敗',data)
                    else:
                        self.status.set(data)
                        messagebox.showinfo('処理完了',data)
        except queue.Empty:pass
        self.after(120,self.poll)


def main():AutoGui().mainloop()
if __name__=='__main__':main()
