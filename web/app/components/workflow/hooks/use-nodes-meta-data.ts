import { useMemo } from 'react'
import type { AvailableNodesMetaData } from '@/app/components/workflow/hooks-store'
import { useHooksStore } from '@/app/components/workflow/hooks-store'
import { BlockEnum } from '@/app/components/workflow/types'
import type { Node, NodeDefault } from '@/app/components/workflow/types'
import { CollectionType } from '@/app/components/tools/types'
import { useStore } from '@/app/components/workflow/store'
import { canFindTool } from '@/utils'
import { useGetLanguage } from '@/context/i18n'
import { BlockClassificationEnum } from '@/app/components/workflow/block-selector/types'

const fallbackNodeMeta: NodeDefault<any> = {
  metaData: {
    classification: BlockClassificationEnum.Default,
    sort: Number.MAX_SAFE_INTEGER,
    type: BlockEnum.Start,
    title: 'Unknown node',
    author: '',
    description: undefined,
    helpLinkUri: undefined,
    isRequired: false,
    isUndeletable: false,
    isStart: false,
    isSingleton: false,
    isTypeFixed: false,
  },
  defaultValue: {},
  checkValid: () => ({ isValid: true, errorMessage: undefined }),
}

const incompleteMetaCache = new WeakMap<NodeDefault<any>, NodeDefault<any>>()

const wrapNodeMeta = (meta?: NodeDefault<any>) => {
  if (!meta)
    return fallbackNodeMeta

  if (typeof meta.checkValid === 'function')
    return meta

  const cached = incompleteMetaCache.get(meta)
  if (cached)
    return cached

  const wrapped: NodeDefault<any> = {
    ...meta,
    checkValid: (...args: Parameters<NodeDefault<any>['checkValid']>) => {
      const original = (meta as NodeDefault<any>).checkValid
      if (typeof original === 'function')
        return original(...args)

      return { isValid: true, errorMessage: undefined }
    },
  }

  incompleteMetaCache.set(meta, wrapped)
  return wrapped
}

export const useNodesMetaData = () => {
  const availableNodesMetaData = useHooksStore(s => s.availableNodesMetaData)

  return useMemo(() => {
    const rawNodesMap = availableNodesMetaData?.nodesMap || {}
    const safeNodesMap = new Proxy(rawNodesMap as Record<string, NodeDefault<any> | undefined>, {
      get(target, prop: string | symbol, receiver) {
        if (typeof prop === 'string')
          return wrapNodeMeta(Reflect.get(target, prop, receiver))

        return Reflect.get(target, prop, receiver)
      },
    }) as unknown as Record<BlockEnum, NodeDefault<any>>

    return {
      nodes: availableNodesMetaData?.nodes || [],
      nodesMap: safeNodesMap,
    } as AvailableNodesMetaData
  }, [availableNodesMetaData])
}

export const useNodeMetaData = (node: Node) => {
  const language = useGetLanguage()
  const buildInTools = useStore(s => s.buildInTools)
  const customTools = useStore(s => s.customTools)
  const workflowTools = useStore(s => s.workflowTools)
  const dataSourceList = useStore(s => s.dataSourceList)
  const availableNodesMetaData = useNodesMetaData()
  const { data } = node
  const nodeMetaData = availableNodesMetaData.nodesMap?.[data.type]
  const author = useMemo(() => {
    if (data.type === BlockEnum.DataSource)
      return dataSourceList?.find(dataSource => dataSource.plugin_id === data.plugin_id)?.author

    if (data.type === BlockEnum.Tool) {
      if (data.provider_type === CollectionType.builtIn)
        return buildInTools.find(toolWithProvider => canFindTool(toolWithProvider.id, data.provider_id))?.author
      if (data.provider_type === CollectionType.workflow)
        return workflowTools.find(toolWithProvider => toolWithProvider.id === data.provider_id)?.author
      return customTools.find(toolWithProvider => toolWithProvider.id === data.provider_id)?.author
    }
    return nodeMetaData?.metaData.author
  }, [data, buildInTools, customTools, workflowTools, nodeMetaData, dataSourceList])

  const description = useMemo(() => {
    if (data.type === BlockEnum.DataSource)
      return dataSourceList?.find(dataSource => dataSource.plugin_id === data.plugin_id)?.description[language]
    if (data.type === BlockEnum.Tool) {
      if (data.provider_type === CollectionType.builtIn)
        return buildInTools.find(toolWithProvider => canFindTool(toolWithProvider.id, data.provider_id))?.description[language]
      if (data.provider_type === CollectionType.workflow)
        return workflowTools.find(toolWithProvider => toolWithProvider.id === data.provider_id)?.description[language]
      return customTools.find(toolWithProvider => toolWithProvider.id === data.provider_id)?.description[language]
    }
    return nodeMetaData?.metaData.description
  }, [data, buildInTools, customTools, workflowTools, nodeMetaData, dataSourceList, language])

  return useMemo(() => {
    return {
      ...nodeMetaData?.metaData,
      author,
      description,
    }
  }, [author, nodeMetaData, description])
}
