'use client'
import { useImperativeHandle } from 'react'
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import type { Dependency, GitHubItemAndMarketPlaceDependency, PackageDependency, Plugin, VersionInfo } from '../../../types'
import MarketplaceItem from '../item/marketplace-item'
import GithubItem from '../item/github-item'
import { useFetchPluginsInMarketPlaceByInfo } from '@/service/use-plugins'
import useCheckInstalled from '@/app/components/plugins/install-plugin/hooks/use-check-installed'
import produce from 'immer'
import PackageItem from '../item/package-item'
import LoadingError from '../../base/loading-error'
import { useGlobalPublicStore } from '@/context/global-public-context'
import { pluginInstallLimit } from '../../hooks/use-install-plugin-limit'

type Props = {
  allPlugins: Dependency[]
  selectedPlugins: Plugin[]
  onSelect: (plugin: Plugin, selectedIndex: number, allCanInstallPluginsLength: number) => void
  onSelectAll: (plugins: Plugin[], selectedIndexes: number[]) => void
  onDeSelectAll: () => void
  onLoadedAllPlugin: (installedInfo: Record<string, VersionInfo>) => void
  isFromMarketPlace?: boolean
  ref?: React.Ref<ExposeRefs>
}

export type ExposeRefs = {
  selectAllPlugins: () => void
  deSelectAllPlugins: () => void
}

const InstallByDSLList = ({
  allPlugins,
  selectedPlugins,
  onSelect,
  onSelectAll,
  onDeSelectAll,
  onLoadedAllPlugin,
  isFromMarketPlace,
  ref,
}: Props) => {
  const systemFeatures = useGlobalPublicStore(s => s.systemFeatures)
  // safe parser for marketplace plugin unique identifier
  const parseMarketplaceIdentifier = useCallback((id?: string): { organization?: string; plugin?: string; version?: string } => {
    if (!id || typeof id !== 'string') return {}
    try {
      const main = id.split('@')[0] || id
      const hasSlash = main.includes('/')
      const [orgMaybe, nameAndVersion] = hasSlash ? main.split('/', 2) : [undefined, main]
      const hasColon = (nameAndVersion || '').includes(':')
      const [plugin, version] = hasColon ? (nameAndVersion as string).split(':', 2) : [nameAndVersion, undefined]
      return { organization: orgMaybe, plugin, version }
    }
    catch {
      return {}
    }
  }, [])
  // DSL has id, to get plugin info to show more info
  const { isLoading: isFetchingMarketplaceDataById, data: infoGetById, error: infoByIdError } = useFetchPluginsInMarketPlaceByInfo(
    allPlugins
      .filter(d => d.type === 'marketplace')
      .map((d) => {
        const dependency = (d as GitHubItemAndMarketPlaceDependency).value
        const { organization, plugin, version } = parseMarketplaceIdentifier(dependency?.marketplace_plugin_unique_identifier)
        if (!plugin)
          return undefined as unknown as { organization: string; plugin: string; version?: string }
        return {
          organization: organization || '',
          plugin,
          version,
        }
      })
      .filter(Boolean) as { organization: string; plugin: string; version?: string }[],
  )
  // has meta(org,name,version), to get id
  const { isLoading: isFetchingDataByMeta, data: infoByMeta, error: infoByMetaError } = useFetchPluginsInMarketPlaceByInfo(allPlugins.filter(d => d.type === 'marketplace').map(d => (d as GitHubItemAndMarketPlaceDependency).value!))

  const [plugins, doSetPlugins] = useState<(Plugin | undefined)[]>((() => {
    const hasLocalPackage = allPlugins.some(d => d.type === 'package')
    if (!hasLocalPackage)
      return []

    const _plugins = allPlugins.map((d) => {
      if (d.type === 'package') {
        return {
          ...(d as any).value.manifest,
          plugin_id: (d as any).value.unique_identifier,
        }
      }

      return undefined
    })
    return _plugins
  })())

  const pluginsRef = React.useRef<(Plugin | undefined)[]>(plugins)

  const setPlugins = useCallback((p: (Plugin | undefined)[]) => {
    doSetPlugins(p)
    pluginsRef.current = p
  }, [])

  const [errorIndexes, setErrorIndexes] = useState<number[]>([])

  const handleGitHubPluginFetched = useCallback((index: number) => {
    return (p: Plugin) => {
      const nextPlugins = produce(pluginsRef.current, (draft) => {
        draft[index] = p
      })
      setPlugins(nextPlugins)
    }
  }, [setPlugins])

  const handleGitHubPluginFetchError = useCallback((index: number) => {
    return () => {
      setErrorIndexes([...errorIndexes, index])
    }
  }, [errorIndexes])

  const marketPlaceInDSLIndex = useMemo(() => {
    const res: number[] = []
    allPlugins.forEach((d, index) => {
      if (d.type === 'marketplace')
        res.push(index)
    })
    return res
  }, [allPlugins])

  useEffect(() => {
    if (!isFetchingMarketplaceDataById && infoGetById?.data.list) {
      const sortedList = allPlugins.filter(d => d.type === 'marketplace').map((d) => {
        const p = d as GitHubItemAndMarketPlaceDependency
        // compute plugin_id safely: org/plugin (without :version)
        const parsed = parseMarketplaceIdentifier(p.value?.marketplace_plugin_unique_identifier)
        const id = parsed.plugin ? (parsed.organization ? `${parsed.organization}/${parsed.plugin}` : parsed.plugin) : undefined
        const retPluginInfo = infoGetById.data.list.find(item => item.plugin.plugin_id === id)?.plugin
        return { ...retPluginInfo, from: d.type } as Plugin
      })
      const payloads = sortedList
      const failedIndex: number[] = []
      const nextPlugins = produce(pluginsRef.current, (draft) => {
        marketPlaceInDSLIndex.forEach((index, i) => {
          if (payloads[i]) {
            draft[index] = {
              ...payloads[i],
              version: payloads[i]!.version || payloads[i]!.latest_version,
            }
          }
          else { failedIndex.push(index) }
        })
      })
      setPlugins(nextPlugins)

      if (failedIndex.length > 0)
        setErrorIndexes([...errorIndexes, ...failedIndex])
    }
  }, [isFetchingMarketplaceDataById])

  useEffect(() => {
    if (!isFetchingDataByMeta && infoByMeta?.data.list) {
      const payloads = infoByMeta?.data.list
      const failedIndex: number[] = []
      const nextPlugins = produce(pluginsRef.current, (draft) => {
        marketPlaceInDSLIndex.forEach((index, i) => {
          if (payloads[i]) {
            const item = payloads[i]
            draft[index] = {
              ...item.plugin,
              plugin_id: item.version.unique_identifier,
            }
          }
          else {
            failedIndex.push(index)
          }
        })
      })
      setPlugins(nextPlugins)
      if (failedIndex.length > 0)
        setErrorIndexes([...errorIndexes, ...failedIndex])
    }
  }, [isFetchingDataByMeta])

  useEffect(() => {
    // get info all failed
    if (infoByMetaError || infoByIdError)
      setErrorIndexes([...errorIndexes, ...marketPlaceInDSLIndex])
  }, [infoByMetaError, infoByIdError])

  const isLoadedAllData = (plugins.filter(p => !!p).length + errorIndexes.length) === allPlugins.length

  const { installedInfo } = useCheckInstalled({
    pluginIds: plugins?.filter(p => !!p).map((d) => {
      return `${d?.org || d?.author}/${d?.name}`
    }) || [],
    enabled: isLoadedAllData,
  })

  const getVersionInfo = useCallback((pluginId: string) => {
    const pluginDetail = installedInfo?.[pluginId]
    const hasInstalled = !!pluginDetail
    return {
      hasInstalled,
      installedVersion: pluginDetail?.installedVersion,
      toInstallVersion: '',
    }
  }, [installedInfo])

  useEffect(() => {
    if (isLoadedAllData && installedInfo)
      onLoadedAllPlugin(installedInfo!)
  }, [isLoadedAllData, installedInfo])

  const handleSelect = useCallback((index: number) => {
    return () => {
      const canSelectPlugins = plugins.filter((p) => {
        const { canInstall } = pluginInstallLimit(p!, systemFeatures)
        return canInstall
      })
      onSelect(plugins[index]!, index, canSelectPlugins.length)
    }
  }, [onSelect, plugins, systemFeatures])

  useImperativeHandle(ref, () => ({
    selectAllPlugins: () => {
      const selectedIndexes: number[] = []
      const selectedPlugins: Plugin[] = []
      allPlugins.forEach((d, index) => {
        const p = plugins[index]
        if (!p)
          return
        const { canInstall } = pluginInstallLimit(p, systemFeatures)
        if (canInstall) {
          selectedIndexes.push(index)
          selectedPlugins.push(p)
        }
      })
      onSelectAll(selectedPlugins, selectedIndexes)
    },
    deSelectAllPlugins: () => {
      onDeSelectAll()
    },
  }))

  return (
    <>
      {allPlugins.map((d, index) => {
        if (errorIndexes.includes(index)) {
          return (
            <LoadingError key={index} />
          )
        }
        const plugin = plugins[index]
        if (d.type === 'github') {
          return (<GithubItem
            key={index}
            checked={!!selectedPlugins.find(p => p.plugin_id === plugins[index]?.plugin_id)}
            onCheckedChange={handleSelect(index)}
            dependency={d as GitHubItemAndMarketPlaceDependency}
            onFetchedPayload={handleGitHubPluginFetched(index)}
            onFetchError={handleGitHubPluginFetchError(index)}
            versionInfo={getVersionInfo(`${plugin?.org || plugin?.author}/${plugin?.name}`)}
          />)
        }

        if (d.type === 'marketplace') {
          return (
            <MarketplaceItem
              key={index}
              checked={!!selectedPlugins.find(p => p.plugin_id === plugins[index]?.plugin_id)}
              onCheckedChange={handleSelect(index)}
              payload={{ ...plugin, from: d.type } as Plugin}
              version={(d as GitHubItemAndMarketPlaceDependency).value.version! || plugin?.version || ''}
              versionInfo={getVersionInfo(`${plugin?.org || plugin?.author}/${plugin?.name}`)}
            />
          )
        }

        // Local package
        return (
          <PackageItem
            key={index}
            checked={!!selectedPlugins.find(p => p.plugin_id === plugins[index]?.plugin_id)}
            onCheckedChange={handleSelect(index)}
            payload={d as PackageDependency}
            isFromMarketPlace={isFromMarketPlace}
            versionInfo={getVersionInfo(`${plugin?.org || plugin?.author}/${plugin?.name}`)}
          />
        )
      })
      }
    </>
  )
}
export default InstallByDSLList
