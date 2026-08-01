import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { AssetGallery } from './AssetGallery'
import { api } from '../../api/client'
import type { GameAsset } from '../../types'

const asset = (over: Partial<GameAsset> = {}): GameAsset =>
    ({ id: 'goblin', kind: 'sprite', status: 'ready', prompt: 'a goblin', defect: null, ...over })

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const gallery = () => render(
    <AssetGallery runId="r1" version={0} rendering={false} canRender onRender={() => {}} acting={false} />,
)

describe('AssetGallery', () => {
    it('previews a ready image from the authed blob route', async () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([asset()])
        vi.spyOn(api, 'getAssetBlobUrl').mockResolvedValue('blob:goblin')

        gallery()

        await waitFor(() => expect(screen.getByAltText('goblin')).toBeTruthy())
        expect(api.getAssetBlobUrl).toHaveBeenCalledWith('r1', 'goblin')
    })

    // A defect is what code could tell was BROKEN about a landed render, never whether it suits
    // the game — so it is shown next to the picture for the person who can answer that.
    it('says what a landed render came back broken as', async () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue(
            [asset({ defect: 'the background was not removed' })])
        vi.spyOn(api, 'getAssetBlobUrl').mockResolvedValue('blob:goblin')

        gallery()

        await waitFor(() => expect(screen.getByText('the background was not removed')).toBeTruthy())
    })

    // A mesh is megabytes and has no in-browser preview, so the gallery must not pull its bytes
    // just to mount a card — they are fetched on the download click instead.
    it('does not fetch a mesh body on mount', async () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([asset({ id: 'tower', kind: 'mesh' })])
        const blob = vi.spyOn(api, 'getAssetBlobUrl').mockResolvedValue('blob:tower')

        gallery()

        await waitFor(() => expect(screen.getByText('tower')).toBeTruthy())
        expect(screen.getByText('GLB ↓')).toBeTruthy()
        expect(blob).not.toHaveBeenCalled()
    })

    it('shows an unrendered asset as queued rather than a broken image', async () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([asset({ status: 'pending' })])
        const blob = vi.spyOn(api, 'getAssetBlobUrl').mockResolvedValue('blob:goblin')

        gallery()

        await waitFor(() => expect(screen.getByText('queued')).toBeTruthy())
        expect(screen.queryByAltText('goblin')).toBeNull()
        expect(blob).not.toHaveBeenCalled()
    })
})
