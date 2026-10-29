import { useEffect, useRef, useState } from "react";
import {
  TransformComponent,
  TransformWrapper,
} from "react-zoom-pan-pinch";
import styles from "./ZoomableProductImage.module.css";

interface ZoomableProductImageProps {
  alt: string;
  imageClassName: string;
  loading?: "eager" | "lazy";
  src: string;
}

export function ZoomableProductImage({
  alt,
  imageClassName,
  loading,
  src,
}: ZoomableProductImageProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (isOpen && !dialog.open) {
      dialog.showModal();
      return;
    }

    if (!isOpen && dialog.open) {
      dialog.close();
    }
  }, [isOpen]);

  function openZoom(): void {
    setIsOpen(true);
  }

  function closeZoom(): void {
    setIsOpen(false);
  }

  return (
    <>
      <button
        className={styles.trigger}
        type="button"
        aria-label={`Zoom in on ${alt}`}
        onClick={openZoom}
      >
        <img
          className={imageClassName}
          src={src}
          alt={alt}
          loading={loading}
          draggable={false}
        />
      </button>

      <dialog
        ref={dialogRef}
        className={styles.viewer}
        aria-label={`Zoomed image: ${alt}`}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          closeZoom();
        }}
        onCancel={(event) => {
          event.preventDefault();
          closeZoom();
        }}
        onClose={closeZoom}
      >
        <button
          className={styles.closeButton}
          type="button"
          onClick={closeZoom}
        >
          Close
        </button>
        {isOpen && (
          <TransformWrapper
            minScale={1}
            initialScale={1}
            maxScale={4}
            centerOnInit
            wheel={{ disabled: true }}
            doubleClick={{ disabled: true }}
          >
            <TransformComponent
              wrapperClass={styles.transformViewport}
              contentClass={styles.transformContent}
            >
              <img
                className={styles.zoomImage}
                src={src}
                alt={alt}
                draggable={false}
              />
            </TransformComponent>
          </TransformWrapper>
        )}
      </dialog>
    </>
  );
}
