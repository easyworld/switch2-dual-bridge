from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest

from controller import pad_state
from pro_controller import (ACTION_ARGUMENTS, DEFAULT_BINDINGS, ProControllerView,
                            assign_binding, clean_bindings, key_from_event,
                            load_bindings, save_bindings)


class BindingFileTests(unittest.TestCase):
    def test_all_old_actions_remain_available_with_valid_payloads(self):
        self.assertEqual(len(ACTION_ARGUMENTS),32)
        for action in ACTION_ARGUMENTS.values():pad_state(**action)
        self.assertEqual(set(DEFAULT_BINDINGS),set(ACTION_ARGUMENTS))
        keys=[key for key in DEFAULT_BINDINGS.values() if key]
        self.assertEqual(len(keys),len(set(keys)))

    def test_conflicting_key_moves_to_the_new_control(self):
        mapping=dict(DEFAULT_BINDINGS)
        self.assertEqual(assign_binding(mapping,'B','j'),'A')
        self.assertEqual(mapping['B'],'j')
        self.assertIsNone(mapping['A'])
        with self.assertRaises(ValueError):assign_binding(mapping,'B','Escape')

    def test_empty_mapping_remains_empty_after_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'gamepad-keymap.json'
            self.assertEqual(load_bindings(path),DEFAULT_BINDINGS)
            save_bindings(path,{})
            self.assertTrue(all(key is None for key in load_bindings(path).values()))
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_invalid_keys_and_duplicate_bindings_are_removed(self):
        mapping=clean_bindings({'A':'j','B':'j','X':'Escape','Y':'Control_L','unknown':'x'})
        self.assertEqual(mapping['A'],'j')
        self.assertIsNone(mapping['B'])
        self.assertIsNone(mapping['X'])
        self.assertIsNone(mapping['Y'])
        self.assertNotIn('unknown',mapping)

    @unittest.skipUnless(sys.platform=='win32','Windows physical key codes')
    def test_physical_keys_ignore_shift_and_capslock(self):
        for symbol,code,expected in (('J',74,'j'),('exclam',49,'1'),('plus',187,'equal'),('underscore',189,'minus')):
            self.assertEqual(key_from_event(SimpleNamespace(keysym=symbol,keycode=code)),expected)


@unittest.skipUnless(sys.platform=='win32','Windows Tk component check')
class ControllerViewTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.calls=[]
        def action(**values):self.calls.append(values);return True
        self.path=Path(self.folder.name)/'gamepad-keymap.json'
        self.view=ProControllerView(self.root,on_action=action,mapping_path=self.path)
        self.view.pack(fill='both',expand=True)
        self.view.set_available(True)

    def tearDown(self):
        self.root.destroy();self.folder.cleanup()

    def key(self,symbol,code=0,state=0):return SimpleNamespace(keysym=symbol,keycode=code,state=state)

    def test_mouse_hit_and_keyboard_repeat_send_one_timed_input(self):
        control=self.view.controls['A']
        x=self.view.offset[0]+control.x*self.view.scale
        y=self.view.offset[1]+control.y*self.view.scale
        self.view._click(SimpleNamespace(x=x,y=y))
        self.assertEqual(self.calls,[{'buttons':'A'}])
        event=self.key('j',74)
        self.view._key_press(event);self.view._key_press(event)
        self.assertEqual(len(self.calls),2)
        self.view._key_release(event);self.view._key_press(event)
        self.assertEqual(len(self.calls),3)

    def test_offline_edit_capture_save_and_reload_never_send_input(self):
        self.view.set_available(False)
        self.view.set_mode('edit')
        self.view.controls['B'].invoke()
        self.view._key_press(self.key('j',74))
        self.assertTrue(self.view.dirty)
        self.assertIsNone(self.view.draft['A'])
        self.assertEqual(self.view.draft['B'],'j')
        self.view.save()
        self.assertEqual(self.view.mode,'click')
        self.assertEqual(load_bindings(self.path)['B'],'j')
        self.assertIsNone(load_bindings(self.path)['A'])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.view.controls['B'].state,'disabled')

    def test_click_mode_uses_saved_bindings_until_edits_are_saved(self):
        self.view.set_mode('edit');self.view.select('B');self.view.bind_selected('j')
        self.view.set_mode('click')
        self.view._key_press(self.key('j',74))
        self.assertEqual(self.calls,[{'buttons':'A'}])
        self.view.set_mode('edit')
        self.assertEqual(self.view.draft['B'],'j')
        self.view.cancel_edit()
        self.assertFalse(self.view.dirty)
        self.assertEqual(self.view.bindings['A'],'j')
        self.assertEqual(self.view.bindings['B'],'k')
        self.assertFalse(self.path.exists())

    def test_clear_reset_and_escape_only_change_the_edit_draft(self):
        self.view.set_mode('edit');self.view.select('A');self.view.clear_selected()
        self.assertIsNone(self.view.draft['A'])
        self.view.reset_defaults()
        self.assertEqual(self.view.draft,DEFAULT_BINDINGS)
        self.view.select('X')
        self.assertEqual(self.view._key_press(self.key('Escape',27)),'break')
        self.assertIsNone(self.view.selected)
        self.assertEqual(self.view.draft,DEFAULT_BINDINGS)
        self.assertEqual(self.calls,[])

    def test_no_global_keyboard_hook_and_modified_keys_do_not_send(self):
        self.assertFalse(self.root.bind('<KeyPress>'))
        self.assertTrue(self.view.canvas.bind('<KeyPress>'))
        self.view._key_press(self.key('j',74,0x04))
        self.assertEqual(self.calls,[])
        self.view.set_available(False)
        self.view._key_press(self.key('j',74))
        self.assertEqual(self.calls,[])

    def test_numlock_allows_capture_and_alt_shortcuts_pass_through(self):
        self.view.set_mode('edit');self.view.select('B')
        self.assertIsNone(self.view._key_press(self.key('F4',115,0x20000)))
        self.assertEqual(self.view.selected,'B')
        self.assertFalse(self.view.dirty)
        self.view._key_press(self.key('j',74,0x08))
        self.assertEqual(self.view.draft['B'],'j')
        self.assertIsNone(self.view.draft['A'])
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
