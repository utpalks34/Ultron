-- EDIT ME: replace with your real apps and contacts.
-- Idempotent: re-running skips rows whose name already exists.

INSERT INTO registry_apps (name, aliases, launch_cmd, process_name, protected) VALUES
  ('Visual Studio Code', '{vscode,code,editor}',      'code',         'Code.exe',           false),
  ('Google Chrome',      '{chrome,browser}',          'chrome',       'chrome.exe',         false),
  ('Microsoft Edge',     '{edge}',                    'msedge',       'msedge.exe',         false),
  ('Notepad',            '{notepad,"text editor"}',   'notepad.exe',  'Notepad.exe',        false),
  ('Calculator',         '{calculator,calc}',         'calc.exe',     'CalculatorApp.exe',  false),
  ('WhatsApp',           '{whatsapp,wa}',             'whatsapp://',  'WhatsApp.Root.exe',  false),
  ('Windows Terminal',   '{terminal,wt,powershell}',  'wt.exe',       'WindowsTerminal.exe', false),
  ('Command Prompt',     '{cmd,"command prompt"}',    'cmd.exe',      'cmd.exe',            false),
  ('File Explorer',      '{explorer,files}',          'explorer.exe', 'explorer.exe',       true),
  ('Task Manager',       '{"task manager"}',          'taskmgr.exe',  'Taskmgr.exe',        true)
ON CONFLICT (name) DO NOTHING;

INSERT INTO registry_contacts (name, aliases, phone, channel) VALUES
  ('Example Person One', '{example1}', '+919999900001', 'whatsapp'),  -- EDIT ME
  ('Example Person Two', '{example2}', '+919999900002', 'whatsapp')   -- EDIT ME
ON CONFLICT (name) DO NOTHING;
