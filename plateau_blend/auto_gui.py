"""User-facing automatic acquisition UI. Tkinter only, pipeline in a worker thread."""
from __future__ import annotations
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from .auto import AutoRequest, preview, generate
from .discovery import blender_candidates, find_nusamai
from .runtime import CancelToken, CancelledError
from .diagnostics import inspect_environment
from .resources import forecast, enforce_budget
from .auto import projected_selection
from .smoke import run_smoke


class AutoGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PLATEAU → Blender | 自動生成 v0.10')
        self.geometry('900x780')
        self.minsize(730,580)
        self.queue=queue.Queue()
        self.vars={}
        self.running=False
        self.cancel_token=None
        self.last_result:Path|None=None
        self.create_widgets()
        self.after(100,self.poll)
        self.after(250,self.scan_dependencies)

    def create_widgets(self):
        container=ttk.Frame(self,padding=12)
        container.pack(expand=True,fill='both')
        container.columnconfigure(1,weight=1)
        row=0
        ttk.Label(container,text='PLATEAUデータ → Blender 自動生成',font=('Yu Gothic UI',15,'bold')).grid(row=row,column=0,columnspan=3,sticky='w',pady=(0,8));row+=1
        ttk.Label(container,text='地域と画質を指定 → GML検索・取得 / OBJ変換 / DEM・航空写真取得 / .blend生成',wraplength=750).grid(row=row,column=0,columnspan=3,sticky='w');row+=1
        self.mode=tk.StringVar(value='bbox')
        ttk.Label(container,text='地域の指定方法').grid(row=row,column=0,sticky='w',pady=4)
        opt=ttk.Frame(container)
        opt.grid(row=row,column=1,columnspan=2,sticky='w')
        for label,value in [('緯度経度の矩形','bbox'),('地名','place'),('メッシュコード','mesh')]:
            ttk.Radiobutton(opt,text=label,variable=self.mode,value=value,command=self.update_area).pack(side='left',padx=6)
        row+=1
        self.area_label=ttk.Label(container,text='範囲 west,south,east,north')
        self.area_label.grid(row=row,column=0,sticky='w',pady=4)
        self.area=tk.StringVar(value='')
        self.area_entry=ttk.Entry(container,textvariable=self.area)
        self.area_entry.grid(row=row,column=1,columnspan=2,sticky='ew');row+=1
        hint='例: 135.42,34.81,135.43,34.82 （西→東、南→北。狭い範囲から試す）'
        self.hint=ttk.Label(container,text=hint,foreground='#786129',wraplength=750)
        self.hint.grid(row=row,column=0,columnspan=3,sticky='w');row+=1
        self.blender_var=tk.StringVar()
        self.nusamai_var=tk.StringVar()
        self.output_var=tk.StringVar()
        home=Path.home()/'Documents'/'PLATEAU'
        self.output_var.set(str(home/'scene.blend'))
        for label,var,filetype in [('Blender本体（自動検出）',self.blender_var,'blender'),
                                   ('nusamai CLI（自動検出）',self.nusamai_var,'nusamai'),
                                   ('保存する .blend',self.output_var,'save')]:
            ttk.Label(container,text=label).grid(row=row,column=0,sticky='w',pady=5)
            ttk.Entry(container,textvariable=var).grid(row=row,column=1,sticky='ew',pady=5)
            ttk.Button(container,text='参照...',command=lambda v=var,t=filetype:self.choose(v,t)).grid(row=row,column=2,padx=(7,0));row+=1
        self.scan_button=ttk.Button(container,text='Blender / CLIを再スキャン',command=self.scan_dependencies)
        self.scan_button.grid(row=row,column=1,sticky='w',pady=(0,7));row+=1
        self.epsg=tk.StringVar(value='AUTO')
        self.lod=tk.StringVar(value='min_lod')
        self.zoom=tk.StringVar(value='16')
        self.dem_res=tk.StringVar(value='100')
        self.axis=tk.StringVar(value='auto')
        for label,var,values in [('平面直角座標系 EPSG（AUTOは地域にも対応）',self.epsg,None),
                                 ('建物LOD',self.lod,('min_lod','max_lod','textured_max_lod')),
                                 ('航空写真ズーム',self.zoom,('14','15','16','17','18')),
                                 ('地形のサンプル密度',self.dem_res,('40','60','100','160','256')),
                                 ('OBJ座標軸（自動優先）',self.axis,('auto','xyz','yxz'))]:
            ttk.Label(container,text=label).grid(row=row,column=0,sticky='w',pady=4)
            if values: control=ttk.Combobox(container,textvariable=var,values=values,state='readonly')
            else: control=ttk.Entry(container,textvariable=var)
            control.grid(row=row,column=1,columnspan=2,sticky='ew',pady=3);row+=1
        self.clip=tk.BooleanVar(value=True)
        ttk.Checkbutton(container,text='矩形指定時は建物を選択範囲で切り出す（境界に切断面を生成しない）',variable=self.clip).grid(row=row,column=0,columnspan=3,sticky='w',pady=4);row+=1
        self.status=tk.StringVar(value='環境スキャン中…')
        self.status_label=ttk.Label(container,textvariable=self.status,wraplength=750)
        self.status_label.grid(row=row,column=0,columnspan=3,sticky='w',pady=(6,1));row+=1
        actions=ttk.Frame(container)
        actions.grid(row=row,column=0,columnspan=3,sticky='ew',pady=6)
        self.preview_button=ttk.Button(actions,text='取得対象の確認',command=self.preview_sources)
        self.preview_button.pack(side='left',padx=3)
        self.generate_button=ttk.Button(actions,text='▶ 自動取得して .blend を生成',command=self.start_generate)
        self.generate_button.pack(side='left',padx=3)
        self.cancel_button=ttk.Button(actions,text='■ 中止',command=self.cancel_running,state='disabled')
        self.cancel_button.pack(side='left',padx=3)
        self.open_button=ttk.Button(actions,text='完成ファイルをBlenderで開く',command=self.open_result,state='disabled')
        self.open_button.pack(side='left',padx=3)
        ttk.Button(actions,text='Blender自己診断',command=self.smoke_test).pack(side='right',padx=3)
        ttk.Button(actions,text='環境診断',command=self.diagnose).pack(side='right',padx=3)
        ttk.Button(actions,text='手動OBJモード',command=self.open_legacy).pack(side='right',padx=3)
        row+=1
        self.progress=ttk.Progressbar(container,mode='indeterminate')
        self.progress.grid(row=row,column=0,columnspan=3,sticky='ew');row+=1
        ttk.Label(container,text='処理ログ / 取得対象').grid(row=row,column=0,columnspan=3,sticky='w',pady=(8,2));row+=1
        frame=ttk.Frame(container)
        frame.grid(row=row,column=0,columnspan=3,sticky='nsew')
        container.rowconfigure(row,weight=1);frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        self.log=tk.Text(frame,height=13,wrap='word')
        self.log.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(frame,command=self.log.yview)
        scrollbar.grid(row=0,column=1,sticky='ns');self.log.configure(yscrollcommand=scrollbar.set)
        row+=1
        ttk.Label(container,text='出典: 国土地理院（全国最新写真・標高タイル）、国土交通省 Project PLATEAU。配布・公開前に利用条件を確認。',foreground='#756125',wraplength=800).grid(row=row,column=0,columnspan=3,sticky='w',pady=4)

    def update_area(self):
        mode=self.mode.get()
        labels={'bbox':('範囲 west,south,east,north','例: 135.42,34.81,135.43,34.82（経度・緯度。小範囲から試す）'),
                'place':('地名','例: 池田市 / 池田駅（地名検索は広範囲の結果を返す場合あり）'),
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
        else:condition='g:'+raw
        return AutoRequest(condition=condition,epsg=self.epsg.get().strip(),
            output_blend=Path(self.output_var.get()).expanduser().resolve(),
            blender_executable=Path(self.blender_var.get()) if self.blender_var.get() else None,
            nusamai_executable=Path(self.nusamai_var.get()) if self.nusamai_var.get() else None,
            lod=self.lod.get(),photo_zoom=int(self.zoom.get()),terrain_resolution=int(self.dem_res.get()),
            axis_override=self.axis.get(),clip_requested_area=self.clip.get())

    def queue_msg(self,s):self.queue.put(('log',str(s)))
    def _run_worker(self,method):
        if self.running:return
        try:request=self.build_request();request.check()
        except Exception as exc:messagebox.showerror('入力エラー',str(exc));return
        self.running=True
        self.cancel_token=CancelToken()
        self.preview_button.configure(state='disabled');self.generate_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.start(14)
        def worker():
            try:method(request)
            except Exception as exc:self.queue.put(('error',f'{type(exc).__name__}: {exc}'))
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

    def start_generate(self):
        try:request=self.build_request()
        except Exception as exc:messagebox.showerror('入力エラー',str(exc));return
        if request.output_blend.is_file():
            messagebox.showerror('上書き禁止','既存 .blend は上書きしない。出力名を変更してね。');return
        def job(req):
            result=generate(req,log=self.queue_msg,cancel=self.cancel_token)
            self.queue.put(('result',str(result)))
        self._run_worker(job)

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
        self.cancel_button.configure(state='normal');self.progress.start(14)
        def worker():
            try:
                output=run_smoke(blender=binary,output_parent=Path(self.output_var.get()).expanduser().resolve().parent/'diagnostics',log=self.queue_msg,cancel=self.cancel_token)
                self.queue.put(('result',str(output)))
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
                    self.status.set(f'Blender {len(blender)}件検出: {blender[0] if blender else "未検出"} | nusamai: {nusamai or "未検出（手動指定可）"}')
                elif kind=='log':
                    self.log.insert('end',data+'\n');self.log.see('end')
                elif kind=='result':
                    self.last_result=Path(data)
                    self.open_button.configure(state='normal')
                else:
                    self.running=False;self.progress.stop()
                    self.preview_button.configure(state='normal');self.generate_button.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                    self.log.insert('end',f'[{kind.upper()}] {data}\n');self.log.see('end')
                    if kind=='error':messagebox.showerror('処理失敗',data)
                    else:messagebox.showinfo('処理完了',data)
        except queue.Empty:pass
        self.after(120,self.poll)


def main():AutoGui().mainloop()
if __name__=='__main__':main()
