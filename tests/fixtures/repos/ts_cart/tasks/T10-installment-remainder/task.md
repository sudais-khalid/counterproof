Bug: installments loses cents. Splitting 10000 cents into 3 instalments returns [3333, 3333, 3333], which adds up to 9999. The last instalment must absorb the remainder: [3333, 3333, 3334].
