"""Windowless entry point for the bundled Windows runtime."""
from pathlib import Path
import sys
import traceback


def main():
    try:
        from portable_integrity import verify_dependencies
        verify_dependencies()
        import app
        app.main()
        return 0
    except Exception:
        try:
            from app_paths import export_root
            (export_root().parent / 'startup_error.log').write_text(traceback.format_exc(), encoding='utf-8')
        except Exception:
            pass
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'აპლიკაციის გაშვება ვერ მოხერხდა. სრულად გაშალეთ ZIP და ხელახლა სცადეთ.\n'
                'შეცდომის დეტალები ინახება startup_error.log ფაილში.',
                'შესყიდვების რეესტრი', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
