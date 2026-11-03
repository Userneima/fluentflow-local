import {DropdownMenu} from '../app/DropdownMenu.jsx';
import {button} from '../lib/ui.js';

export const PLAYBACK_SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];

// The page's own speed menu. The browser's player closes its built-in menus
// whenever anything on the page scrolls, and the transcript scrolls on every
// new sentence, so a speed could not be picked there during playback
// (2026-11-03). This menu is closed only by a choice, Escape or a click
// outside it.
const PlaybackSpeedMenu = ({rate, onChange, lang = 'zh', align = 'left'}) => (
    <DropdownMenu
        align={align}
        trigger={(
            <button type="button" aria-label={lang === 'zh' ? '播放速度' : 'Playback speed'} title={lang === 'zh' ? '播放速度' : 'Playback speed'} className={button('secondary', 'sm')}>
                <span className="tabular-nums">{`${rate}×`}</span>
            </button>
        )}
        items={PLAYBACK_SPEEDS.map((speed) => ({
            label: `${speed}×`,
            badge: speed === rate ? '✓' : '',
            onClick: () => onChange(speed),
        }))}
    />
);

export default PlaybackSpeedMenu;
