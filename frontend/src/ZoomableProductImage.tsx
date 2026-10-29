import { useState } from "react";
import {
  TransformComponent,
  TransformWrapper,
} from "react-zoom-pan-pinch";
import styles from "./ZoomableProductImage.module.css";

interface ZoomableProductImageProps {
  alt: string;
  imageClassName: string;
  src: string;
}

export function ZoomableProductImage({
  alt,
  imageClassName,
  src,
}: ZoomableProductImageProps) {
  const [isZoomed, setIsZoomed] = useState(false);

  return (
    <TransformWrapper
      minScale={1}
      initialScale={1}
      maxScale={4}
      centerOnInit
      wheel={{ disabled: true }}
      doubleClick={{ disabled: true }}
      panning={{ disabled: !isZoomed }}
      onTransform={(_transformRef, transformState) => {
        const shouldEnablePanning = transformState.scale > 1;
        setIsZoomed((currentlyZoomed) =>
          currentlyZoomed === shouldEnablePanning
            ? currentlyZoomed
            : shouldEnablePanning,
        );
      }}
    >
      <TransformComponent
        wrapperClass={styles.transformViewport}
        contentClass={styles.transformContent}
      >
        <img
          className={`${imageClassName} ${styles.zoomImage}`}
          src={src}
          alt={alt}
          draggable={false}
        />
      </TransformComponent>
    </TransformWrapper>
  );
}
