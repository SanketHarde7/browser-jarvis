/**
 * 🔌 WebSocket Hook
 * Manages persistent WS connection with event-based message handling
 * Supports voice, text, image, and ping/pong keepalive
 */
import { useState, useEffect, useRef, useCallback } from 'react'

export function useWebSocket(url, { onEvent } = {}) {
  const [isConnected, setIsConnected] = useState(false)
  const wsRef = useRef(null)
  const hasGreeted = useRef(false)
  const reconnectTimeoutRef = useRef(null)
  const pingIntervalRef = useRef(null)
  const onEventRef = useRef(onEvent)

  // Keep onEvent ref updated
  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    let socket
    let isMounted = true

    const connect = () => {
      if (!isMounted) return
      if (wsRef.current?.readyState === WebSocket.OPEN) return

      try {
        socket = new WebSocket(url)

        socket.onopen = () => {
          if (!isMounted) return
          console.log('🔌 WebSocket connected')
          setIsConnected(true)

          if (!hasGreeted.current) {
            hasGreeted.current = true
            setTimeout(() => {
              if (socket?.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: 'request_greeting' }))
              }
            }, 500)
          }

          // Keepalive ping every 25 seconds
          if (pingIntervalRef.current) clearInterval(pingIntervalRef.current)
          pingIntervalRef.current = setInterval(() => {
            if (socket?.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify({ type: 'ping' }))
            }
          }, 25000)
        }

        socket.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            onEventRef.current?.(data)
          } catch (err) {
            console.error('WS parse error:', err)
          }
        }

        socket.onclose = () => {
          if (!isMounted) return
          console.log('🔌 WebSocket disconnected')
          setIsConnected(false)
          if (pingIntervalRef.current) clearInterval(pingIntervalRef.current)
          // Auto-reconnect after 3 seconds
          reconnectTimeoutRef.current = setTimeout(connect, 3000)
        }

        socket.onerror = (err) => {
          console.error('WebSocket error:', err)
          setIsConnected(false)
        }

        wsRef.current = socket
      } catch (err) {
        console.error('WS connection failed:', err)
        setIsConnected(false)
        reconnectTimeoutRef.current = setTimeout(connect, 3000)
      }
    }

    connect()

    return () => {
      isMounted = false
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current)
      if (socket?.readyState === WebSocket.OPEN) {
        socket.close(1000, 'Component unmount')
      }
    }
  }, [url])

  // Send voice audio
  const sendVoice = useCallback((audioBase64, timestamp) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: 'voice', audio: audioBase64, timestamp: timestamp || Date.now() })
      )
      return true
    }
    console.warn('WebSocket not ready for voice')
    return false
  }, [])

  // Send text message
  const sendText = useCallback((message) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: 'text', message })
      )
      return true
    }
    console.warn('WebSocket not ready for text')
    return false
  }, [])

  // ── NEW: Send Image Data ──
  const sendImage = useCallback((base64Data, prompt) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ 
          type: 'image', 
          image_data: base64Data, 
          prompt: prompt || "What is in this image?" 
        })
      )
      return true
    }
    console.warn('WebSocket not ready for image')
    return false
  }, [])

  return { isConnected, sendVoice, sendText, sendImage }
}