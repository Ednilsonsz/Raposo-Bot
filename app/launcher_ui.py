import tkinter as tk
from tkinter import messagebox, filedialog
from credential_store import load_credentials, save_credentials

class LauncherUI:
    def __init__(self, cfg=None):
        self.cfg=cfg or {}; self.action=None; self.folder=(self.cfg.get('drive_sync') or {}).get('folder','')
        user,pwd=load_credentials()
        if not user:
            user='ednilsonsz@gmail.com'
        self.build_label=str(self.cfg.get('build_revision') or ('V'+str(self.cfg.get('version','3.80'))+' · R12'))
        bg='#050b13'; card='#0b1522'; field='#07111d'; border='#1d354c'
        text='#dce8f5'; muted='#8195aa'; accent='#9cff25'; danger='#ff6685'
        self.root=tk.Tk(); self.root.title('Raposo Bot '+self.build_label)
        self.root.geometry('560x640'); self.root.resizable(False,False); self.root.configure(bg=bg)
        self.root.protocol('WM_DELETE_WINDOW', self.cancel)

        def label(parent, value, size=9, weight='normal', color=text, **kwargs):
            return tk.Label(parent,text=value,font=('Segoe UI',size,weight),fg=color,bg=parent.cget('bg'),**kwargs)
        def entry(parent, variable, show=None, readonly=False, width=None):
            return tk.Entry(parent,textvariable=variable,show=show or '',width=width,font=('Segoe UI',10),
                            relief='flat',bd=0,bg=field,fg=text,insertbackground=accent,
                            readonlybackground=field,disabledbackground=field,disabledforeground=muted,
                            highlightthickness=1,highlightbackground=border,highlightcolor=accent,
                            state='readonly' if readonly else 'normal')
        def section(title, subtitle=None):
            frame=tk.Frame(body,bg=card,highlightthickness=1,highlightbackground=border,padx=12,pady=7)
            frame.pack(fill='x',pady=(0,6))
            label(frame,title,10,'bold').pack(anchor='w')
            if subtitle: label(frame,subtitle,8,color=muted).pack(anchor='w',pady=(1,8))
            return frame

        header=tk.Frame(self.root,bg=bg,padx=18,pady=10);header.pack(fill='x')
        brand=tk.Frame(header,bg=bg);brand.pack(fill='x')
        label(brand,'RAPOSO',18,'bold',accent).pack(side='left')
        pill=tk.Label(brand,text=self.build_label,font=('Segoe UI',9,'bold'),fg=accent,bg='#142315',padx=10,pady=4)
        pill.pack(side='right')
        label(header,'Execução DEMO · controle automático e seguro por turno',9,color=muted).pack(anchor='w',pady=(4,0))
        tk.Frame(self.root,bg=accent,height=2).pack(fill='x')
        content=tk.Frame(self.root,bg=bg);content.pack(fill='both',expand=True)
        canvas=tk.Canvas(content,bg=bg,highlightthickness=0,bd=0)
        scrollbar=tk.Scrollbar(content,orient='vertical',command=canvas.yview,bg=card,troughcolor=bg,activebackground=accent)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        body=tk.Frame(canvas,bg=bg,padx=18,pady=8)
        body_window=canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(body_window,width=event.width))
        canvas.bind_all('<MouseWheel>',lambda event:canvas.yview_scroll(int(-event.delta/120),'units'))

        login=section('ACESSO BULLEX','Credenciais protegidas pelo Windows (DPAPI).')
        login_grid=tk.Frame(login,bg=card);login_grid.pack(fill='x')
        login_grid.columnconfigure(0,weight=1);login_grid.columnconfigure(1,weight=1)
        self.user=tk.StringVar(value=user); self.pwd=tk.StringVar(value=pwd)
        label(login_grid,'E-mail',8,color=muted).grid(row=0,column=0,sticky='w')
        label(login_grid,'Senha',8,color=muted).grid(row=0,column=1,sticky='w',padx=(10,0))
        user_entry=entry(login_grid,self.user);user_entry.grid(row=1,column=0,sticky='ew',ipady=5,pady=(3,0))
        pwd_entry=entry(login_grid,self.pwd,show='•');pwd_entry.grid(row=1,column=1,sticky='ew',ipady=5,padx=(10,0),pady=(3,0))

        target=section('STOP LOSS AUTOMÁTICO','Informe LIVRE ou um número inteiro de LOSS. WIN nunca interrompe o robô.')
        shifts=(self.cfg.get('loss_control') or {}).get('shifts') or {}
        defaults={f'T{i}':{'mode':'LIMIT','limit':2} for i in range(1,9)}
        labels={f'T{i+1}':f'T{i+1}  {i*3:02d}–{((i+1)*3)%24:02d}h' for i in range(8)}
        self.shift_values={}
        target_grid=tk.Frame(target,bg=card);target_grid.pack(fill='x')
        [target_grid.columnconfigure(i,weight=1,uniform='shifts') for i in range(4)]
        for row,name in enumerate(defaults):
            item=shifts.get(name) or defaults[name]
            value='LIVRE' if str(item.get('mode') or '').upper()=='FREE' else (str(item.get('limit')) if item.get('limit') else '')
            variable=tk.StringVar(value=value); self.shift_values[name]=variable
            cell=tk.Frame(target_grid,bg=card);cell.grid(row=row//4,column=row%4,sticky='ew',padx=(0,4),pady=(2,7))
            label(cell,labels[name],8,'bold',muted).pack(anchor='w')
            shift_entry=entry(cell,variable,width=8);shift_entry.pack(fill='x',ipady=4,pady=(3,0))

        daily_row=tk.Frame(target,bg=card);daily_row.pack(fill='x',pady=(2,0))
        label(daily_row,'LIMITE DIÁRIO LÍQUIDO · USD',8,'bold',muted).pack(side='left')
        self.daily_limit=tk.StringVar(value=f"{int((self.cfg.get('risk_return') or {}).get('daily_net_loss_minor',4000))/100:.2f}")
        entry(daily_row,self.daily_limit,width=10).pack(side='right',ipady=4)
        label(target,'A trava diária permanece após reiniciar e libera no próximo dia.',8,color=muted).pack(anchor='w',pady=(3,0))

        # Parâmetros operacionais visíveis antes de armar
        params=section('PARÂMETROS OPERACIONAIS','Expiração fixa em M1; a stake permanece sob validação do motor.')
        params_grid=tk.Frame(params,bg=card);params_grid.pack(fill='x')
        params_grid.columnconfigure(0,weight=1);params_grid.columnconfigure(1,weight=1)
        self.stake=tk.StringVar(value=f"{float(self.cfg.get('demo_stake',10.0)):.2f}")
        self.expiry=tk.StringVar(value='1 min')
        stake_box=tk.Frame(params_grid,bg=card);stake_box.grid(row=0,column=0,sticky='ew',padx=(0,8))
        expiry_box=tk.Frame(params_grid,bg=card);expiry_box.grid(row=0,column=1,sticky='ew',padx=(8,0))
        label(stake_box,'VALOR DA ENTRADA',8,'bold',muted).pack(anchor='w')
        self.ent_stake=entry(stake_box,self.stake);self.ent_stake.pack(fill='x',ipady=5,pady=(3,0))
        label(expiry_box,'EXPIRAÇÃO',8,'bold',muted).pack(anchor='w')
        self.ent_exp=entry(expiry_box,self.expiry,readonly=True);self.ent_exp.pack(fill='x',ipady=5,pady=(3,0))

        storage=section('BANCO E SINCRONIZAÇÃO','Histórico real em Raposo_Data; nenhuma limpeza ou zeragem é realizada.')
        choose=tk.Button(storage,text='SELECIONAR PASTA',font=('Segoe UI',8,'bold'),command=self.choose,
                         relief='flat',bd=0,bg='#17304a',fg=text,activebackground='#21466b',activeforeground=accent,
                         cursor='hand2',padx=14,pady=7)
        choose.pack(anchor='w')
        self.path=tk.StringVar(value=self.folder or 'Nenhuma pasta selecionada')
        path_label=tk.Label(storage,textvariable=self.path,anchor='w',justify='left',wraplength=500,
                            font=('Segoe UI',8),fg=muted,bg=card)
        path_label.pack(fill='x',pady=(7,0))

        footer=tk.Frame(self.root,bg=bg,padx=18,pady=8);footer.pack(fill='x',side='bottom')
        self.demo_ok=tk.BooleanVar(value=True)
        check=tk.Checkbutton(footer,text='Confirmo que a conta selecionada é DEMO',variable=self.demo_ok,
                             font=('Segoe UI',9,'bold'),fg=text,bg=bg,activebackground=bg,activeforeground=accent,
                             selectcolor=field,highlightthickness=0)
        check.pack(anchor='w',pady=(0,6))
        actions=tk.Frame(footer,bg=bg);actions.pack(fill='x')
        start=tk.Button(actions,text='INICIAR DEMO',font=('Segoe UI',11,'bold'),command=self.arm,
                        relief='flat',bd=0,bg=accent,fg='#061006',activebackground='#baff62',activeforeground='#061006',
                        cursor='hand2',pady=8)
        start.pack(side='left',fill='x',expand=True,padx=(0,6))
        close=tk.Button(actions,text='ENCERRAR',font=('Segoe UI',9,'bold'),command=self.cancel,
                        relief='flat',bd=0,bg='#24131b',fg=danger,activebackground='#3a1b27',activeforeground='#ff9bad',
                        cursor='hand2',pady=8)
        close.pack(side='left',fill='x',expand=True,padx=(6,0))
    def choose(self):
        p=filedialog.askdirectory(title='Selecione Raposo_Data\\database')
        if p: self.folder=p; self.path.set(p)
    def arm(self):
        if not self.demo_ok.get():
            messagebox.showerror('DEMO obrigatório','Esta versão só pode ser armada para conta DEMO.')
            return
        if not self.folder:
            if not messagebox.askyesno('Drive não configurado','Nenhuma pasta sincronizada foi selecionada. Continuar somente com banco local?'): return
        shift_config={}
        try:
            for name,variable in self.shift_values.items():
                raw=variable.get().strip().upper()
                if raw=='LIVRE':
                    shift_config[name]={'mode':'FREE','limit':None}
                else:
                    limit=int(raw)
                    if limit <= 0: raise ValueError(name)
                    shift_config[name]={'mode':'LIMIT','limit':limit}
            stake=float(self.stake.get().replace(',','.')); assert stake == 10.0
            from decimal import Decimal
            daily=Decimal(self.daily_limit.get().strip().replace(',','.'))
            if not daily.is_finite() or daily < Decimal('10') or daily.as_tuple().exponent < -2: raise ValueError('limite diario')
            daily_minor=int(daily*100)
        except Exception:
            messagebox.showerror('Turnos inválidos','Informe limites de LOSS positivos, entrada fixa 10 e limite diário de pelo menos USD 10 (até 2 decimais).' ); return
        try: save_credentials(self.user.get().strip(),self.pwd.get())
        except Exception as e: messagebox.showwarning('Credenciais',f'Não foi possível salvar a senha com DPAPI: {e}')
        self.action='arm'; self.target={'enabled':True,'timezone':'America/Sao_Paulo','shifts':shift_config,'count_from_shift_start':True}; self.operating={'stake':stake,'expiry_minutes':1,'daily_net_loss_minor':daily_minor}; self.root.destroy()
    def cancel(self): self.action='cancel'; self.target=None; self.operating=None; self.root.destroy()
    def run(self):
        self.root.mainloop()
        return self.action, self.folder, 'LOSS_SHIFTS', self.target, self.operating, self.user.get().strip(), self.pwd.get()

def confirm_exit_dialog():
    result={'ok':False}; root=tk.Tk(); root.title('Raposo Bot · Encerrar'); root.geometry('390x190'); root.resizable(False,False); root.configure(bg='#07111e')
    try: root.attributes('-topmost',True)
    except: pass
    def finish(v):
        result['ok']=bool(v)
        try: root.grab_release()
        except: pass
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',lambda:finish(False)); outer=tk.Frame(root,bg='#07111e',padx=20,pady=18); outer.pack(fill='both',expand=True)
    tk.Label(outer,text='ENCERRAR ROBÔ?',font=('Segoe UI',13,'bold'),fg='#ff3b73',bg='#07111e').pack(anchor='w')
    tk.Label(outer,text='A execução DEMO será interrompida.\nUm último snapshot válido do banco será salvo no Drive.',justify='left',font=('Segoe UI',9),fg='#a8bed0',bg='#07111e').pack(anchor='w',pady=(10,18))
    b=tk.Frame(outer,bg='#07111e'); b.pack(fill='x')
    tk.Button(b,text='CANCELAR',height=2,command=lambda:finish(False)).pack(side='left',fill='x',expand=True,padx=(0,6))
    tk.Button(b,text='SIM, ENCERRAR',height=2,command=lambda:finish(True)).pack(side='left',fill='x',expand=True,padx=(6,0))
    root.update_idletasks(); sw,sh=root.winfo_screenwidth(),root.winfo_screenheight(); w,h=root.winfo_width(),root.winfo_height(); root.geometry(f'{w}x{h}+{(sw-w)//2}+{(sh-h)//2}'); root.lift(); root.focus_force(); root.grab_set(); root.mainloop(); return result['ok']
