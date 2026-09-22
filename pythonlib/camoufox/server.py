import subprocess
from pathlib import Path
from typing import Any, Dict, NoReturn, Tuple, Union

import base64
import orjson
from playwright._impl._driver import compute_driver_executable

from camoufox.pkgman import LOCAL_DATA
from camoufox.utils import launch_options, spoofs_window_dimensions

LAUNCH_SCRIPT: Path = LOCAL_DATA / "launchServer.js"


def camel_case(snake_str: str) -> str:
    """
    Convert a string to camelCase
    """
    if len(snake_str) < 2:
        return snake_str
    camel_case_str = ''.join(x.capitalize() for x in snake_str.lower().split('_'))
    return ("_" if snake_str[0] == "_" else "") + camel_case_str[0].lower() + camel_case_str[1:]


def to_camel_case_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a dictionary to camelCase
    """
    return {camel_case(key): value for key, value in data.items()}


def get_nodejs() -> str:
    """
    Get the bundled Node.js executable
    """
    # Note: Older versions of Playwright return a string rather than a tuple.
    _nodejs: Union[str, Tuple[str, ...]] = compute_driver_executable()[0]
    if isinstance(_nodejs, tuple):
        return _nodejs[0]
    return _nodejs


def launch_server(**kwargs) -> NoReturn:
    """
    Launch a Playwright server. Takes the same arguments as `Camoufox()`.
    Prints the websocket endpoint to the console.

    Persistent profiles are servable: pass `user_data_dir` (with or without
    `persistent_context=True`). The server then runs in Playwright's shared
    mode, and clients reach the persistent profile through
    `browser.contexts[0]` after `firefox.connect()`. Contexts created with
    `browser.new_context()` are ephemeral and are NOT saved to the profile.

    Shared mode has two consequences for clients: contexts created with
    `new_context()` are visible to every client and are not closed when the
    creating client disconnects, and closing `browser.contexts[0]` shuts the
    persistent browser down for everyone.
    """
    # fix-user-data: a persistent profile IS servable. Playwright's
    # BrowserServerLauncherImpl.launchServer() honours the private
    # `_userDataDir` option and calls launchPersistentContext(). On its own
    # that is not enough: in the default "launchServer" mode every client
    # connection gets isolated contexts and never sees the persistent default
    # context, so nothing it does is saved. `_sharedBrowser` selects
    # "launchServerShared", which exposes that context as browser.contexts[0].
    # Both private options exist in playwright-core 1.58-1.62 (pythonlib pins
    # playwright<1.63). launch_options() emits `_user_data_dir`, and
    # camel_case() keeps the leading underscore (-> `_userDataDir`,
    # `_sharedBrowser`).
    if kwargs.pop('persistent_context', None) and not kwargs.get('user_data_dir'):
        # Don't accept the option and silently serve a throwaway profile.
        raise ValueError("launch_server(persistent_context=True) requires user_data_dir")

    config = launch_options(**kwargs)
    if config.get('_user_data_dir'):
        config['_shared_browser'] = True
        # Server-side counterpart of NewBrowser's #666 default: the persistent
        # context is created here, so Playwright would give it its 1280x720
        # default viewport, contradicting the spoofed window dimensions (and
        # clients cannot change it on contexts[0] afterwards).
        if spoofs_window_dimensions(config) and not any(
            key in config for key in ('viewport', 'no_viewport', 'no_default_viewport')
        ):
            config['no_default_viewport'] = True
    nodejs = get_nodejs()

    data = orjson.dumps(to_camel_case_dict(config))

    # The Playwright driver's package directory, which bundles playwright-core.
    driver_package = Path(nodejs).parent / "package"

    process = subprocess.Popen(  # nosec
        [
            nodejs,
            str(LAUNCH_SCRIPT),
            str(driver_package),
        ],
        cwd=driver_package,
        stdin=subprocess.PIPE,
        text=True,
    )
    # Send one newline-delimited configuration frame, then keep the pipe open.
    # The Node process treats EOF as a shutdown request and can close Playwright
    # cleanly before exiting, which removes its temporary browser profile.
    assert process.stdin is not None
    stdin = process.stdin
    encoded_config = base64.b64encode(data).decode() + '\n'

    def close_stdin() -> None:
        if stdin.closed:
            return
        try:
            stdin.close()
        except OSError:
            pass

    def shutdown_process() -> None:
        """Ask Node to close BrowserServer, then reap it with bounded fallbacks."""
        close_stdin()
        if process.poll() is not None:
            process.wait()
            return
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    try:
        stdin.write(encoded_config)
        stdin.flush()
        process.wait()
    except OSError as error:
        # If Node failed before reading the frame, preserve its exit status
        # instead of masking the original failure with a pipe exception.
        shutdown_process()
        raise RuntimeError(
            f"Server process terminated unexpectedly with exit code {process.returncode}"
        ) from error
    except BaseException:
        shutdown_process()
        raise
    finally:
        close_stdin()

    # Add an explicit exception to satisfy the NoReturn type hint.
    raise RuntimeError(
        f"Server process terminated unexpectedly with exit code {process.returncode}"
    )
