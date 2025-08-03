### Настройка стартовой страницы в Librewolf (Arch Linux)  
**Цель:** Заменить стандартную стартовую страницу на пользовательский HTML-файл.

❗️ После изменения стартовой страницы, происходит разлогивание на всех сайтах. 

---

#### Шаг 1: Создание файла `autoconfig.js`  
```bash
sudo nano /usr/lib/librewolf/defaults/pref/autoconfig.js
```  
**Содержимое файла:**  
```javascript
pref("general.config.filename", "librewolf.cfg");
pref("general.config.obscure_value", 0);
pref("general.config.sandbox_enabled", false);
```  
**Проверка прав:**  
```bash
cd /usr/lib/librewolf/defaults/pref/
stat --format='%a %n' autoconfig.js  # Должно быть: 644
```  

---

#### Шаг 2: Редактирование файла `librewolf.cfg`  
```bash
sudo nano /usr/lib/librewolf/librewolf.cfg
```  
**Добавить в самое начало файла:**  
```javascript

null;

//
try {
  const ctx = {};
  ChromeUtils.defineESModuleGetters(ctx, {
    AboutNewTab: "resource:///modules/AboutNewTab.sys.mjs"
  });
  ctx.AboutNewTab.newTabURL = "file:///home/New_tab/index.html";
} catch (e) {
  ChromeUtils.reportError(e);
}
/** ------------------------------
* My settings. Changes base page.
* ------------------------------- */
```  
**Важно:**  
- Первая строка — пустая (а потом уже `null;` и остальное). 
- Путь `file:///home/New_tab/index.html` заменить на свой. 
- Проверка прав:  
  ```bash
  cd /usr/lib/librewolf/
  stat --format='%a %n' librewolf.cfg  # Должно быть: 644
  ```  

---

#### Шаг 3: Настройка страницы внутри Librewolf

В настройках `about:preferences#home` в качестве домашней страницы укажите путь `file:///home/New_tab/index.html`

---

#### Шаг 4: Перезапуск Librewolf  
Закройте все процессы браузера и запустите его снова. При открытии новой вкладки будет загружена страница:  
`file:///home/New_tab/index.html`.  

> **Примечание:**  
> - Убедитесь, что путь к HTML-файлу корректен.  
> - Для отката удалите добавленные строки из `librewolf.cfg`.
