// @vitest-environment jsdom

// The text model field. Requirements (2026-10-10): any model name can be used,
// including one released after this build, and the provider's current models
// are offered as suggestions once a key is saved.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';

const getProviderModels = vi.fn();
vi.mock('../app/shared.jsx', async (importOriginal) => ({
    ...(await importOriginal()),
    useApi: () => ({getProviderModels}),
}));

const {ProviderModelField} = await import('./settingsRows.jsx');

afterEach(() => { cleanup(); getProviderModels.mockReset(); });

describe('ProviderModelField', () => {
    it('saves a typed model name', () => {
        getProviderModels.mockResolvedValue([]);
        const onChange = vi.fn();
        render(<ProviderModelField provider="openai" value="gpt-5.4-mini" configured onChange={onChange}/>);
        const field = screen.getByLabelText('model');
        fireEvent.change(field, {target: {value: 'gpt-9-mini'}});
        fireEvent.blur(field);
        expect(onChange).toHaveBeenCalledWith('gpt-9-mini');
    });

    it('offers the provider list once a key is saved, and asks for nothing without one', async () => {
        getProviderModels.mockResolvedValue(['gpt-9', 'gpt-9-mini']);
        const {container, rerender} = render(<ProviderModelField provider="openai" value="" configured={false} onChange={() => {}}/>);
        expect(getProviderModels).not.toHaveBeenCalled();
        rerender(<ProviderModelField provider="openai" value="" configured onChange={() => {}}/>);
        await waitFor(() => expect(container.querySelectorAll('datalist option')).toHaveLength(2));
        expect(getProviderModels).toHaveBeenCalledWith('openai');
    });
});
